use std::collections::VecDeque;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::thread;
use std::time::{Duration, Instant};

use lrcplus_audio_dsp_v1::LiveRate;

use crate::{
    equalizer::{Equalizer, Settings as EqSettings},
    media::{Decoder, Packet, TransportSource},
    mix,
    network::NetworkSource,
    output::Device,
    pitch::{PitchStage, BLOCK_FRAMES},
    tap::Tap,
    Options, Result, MAX_TRANSPORT_GAIN,
};

const REPORT_INTERVAL: Duration = Duration::from_millis(33);
const DEVICE_TIMEOUT: Duration = Duration::from_secs(5);
const VERSION: u32 = 6;

enum Command {
    Play,
    Pause,
    Seek(u64, u64),
    Next(u64, u64, f32, PathBuf),
    ClearNext(u64),
    Accept(u64, u64),
    Speed(f64),
    Pitch(f64),
    Volume(f32),
    Normalization(f32),
    Equalizer(EqSettings),
    Observe(bool),
    Crossfade(u64),
    Quit,
}

impl Command {
    fn parse(line: &str) -> Option<Self> {
        if let Some(request) = line.strip_prefix("next ") {
            let mut fields = request.splitn(4, ' ');
            let epoch = fields.next()?.parse().ok()?;
            let id = fields.next()?.parse().ok()?;
            let gain = normalization(fields.next()?)?;
            let path = fields.next().filter(|path| !path.is_empty())?.into();
            return Some(Self::Next(epoch, id, gain, path));
        }
        let mut fields = line.split_whitespace();
        let command = match fields.next()? {
            "play" => Self::Play,
            "pause" => Self::Pause,
            "seek" => Self::Seek(fields.next()?.parse().ok()?, fields.next()?.parse().ok()?),
            "clear-next" => Self::ClearNext(fields.next()?.parse().ok()?),
            "accept" => Self::Accept(fields.next()?.parse().ok()?, fields.next()?.parse().ok()?),
            "normalization" => Self::Normalization(normalization(fields.next()?)?),
            "equalizer" => return EqSettings::parse(fields).map(Self::Equalizer),
            "observe" => Self::Observe(match fields.next()? {
                "0" => false,
                "1" => true,
                _ => return None,
            }),
            "crossfade" => Self::Crossfade(fields.next()?.parse().ok().filter(|ms| *ms <= 10_000)?),
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

fn normalization(value: &str) -> Option<f32> {
    let gain = value.parse().ok()?;
    (0.0..=1000.0).contains(&gain).then_some(gain)
}

fn read_commands() -> mpsc::Receiver<Command> {
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
    commands
}

pub fn run(options: Options) -> Result<()> {
    let commands = read_commands();
    let mut decoder: Box<dyn TransportSource> = if options.stream_ffmpeg.is_some() {
        Box::new(NetworkSource::open(&options)?)
    } else {
        Box::new(Decoder::open(&options.path)?)
    };
    let mut gain = options.volume;
    let mut speed = options.speed;
    let mut semitones = options.pitch;
    let library = options.pitch_library.as_deref();
    let endpoint = options.device.as_deref();
    let mut output = Output::new(decoder.as_ref(), speed, gain, library, semitones, endpoint)?;
    let mut equalizer = Equalizer::default();
    let mut tap = Tap::default();
    let mut crossfade_ms = 0;
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    writeln!(
        stdout,
        "ready {} {} {VERSION}",
        decoder.duration(),
        output.device.sample_rate
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
            output.device.check_device()?;
            let should_wait = !output.pump(
                decoder.as_mut(),
                offset,
                crossfade_ms,
                &mut equalizer,
                &mut tap,
            )?;
            let frames = output.device.audible_frames();
            if frames > heard_frames {
                heard_frames = frames;
                advanced = Instant::now();
            }
            let mut position = output.position(frames, offset, decoder.duration());
            if output.report_transition(&mut stdout, epoch, frames)? {
                reported = Instant::now();
            } else if output.drained() {
                output.device.pause()?;
                playing = false;
                if decoder.duration() != 0 {
                    position = decoder.duration();
                }
                output.report(&mut stdout, "ended", epoch, position)?;
            } else if advanced.elapsed() >= DEVICE_TIMEOUT {
                return Err("the output device stopped playing audio".into());
            } else if reported.elapsed() >= REPORT_INTERVAL {
                tap.report(epoch, output.device.sample_rate, output.device.channels);
                if output.next.as_ref().is_none_or(|next| !next.reported) {
                    output.report(&mut stdout, "position", epoch, position)?;
                }
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
                output.device.pause()?;
                playing = false;
                let frames = output.device.audible_frames();
                if output.next.as_ref().is_none_or(|next| !next.reported) {
                    let position = output.position(frames, offset, decoder.duration());
                    output.report(&mut stdout, "position", epoch, position)?;
                }
            }
            Command::Seek(next_epoch, milliseconds) => {
                (output, offset) = output.seek(
                    decoder.as_mut(),
                    milliseconds,
                    gain,
                    library,
                    semitones,
                    endpoint,
                )?;
                tap.clear();
                equalizer.reset(output.device.sample_rate, output.device.channels);
                epoch = next_epoch;
                heard_frames = 0;
                advanced = Instant::now();
                output.report(&mut stdout, "position", epoch, offset)?;
            }
            Command::ClearNext(request_epoch) if request_epoch == epoch => {
                if output.next.take().is_some_and(|next| {
                    next.track.clock.end() != 0 || !next.overlap.outgoing.is_empty()
                }) {
                    let frames = output.device.audible_frames();
                    let position = output.position(frames, offset, decoder.duration());
                    (output, offset) = output.seek(
                        decoder.as_mut(),
                        position,
                        gain,
                        library,
                        semitones,
                        endpoint,
                    )?;
                    tap.clear();
                    equalizer.reset(output.device.sample_rate, output.device.channels);
                    heard_frames = 0;
                    advanced = Instant::now();
                    output.report(&mut stdout, "position", epoch, offset)?;
                }
            }
            Command::Next(request_epoch, id, gain, path)
                if request_epoch == epoch
                    && output.next.is_none()
                    && library.is_none()
                    && options.stream_ffmpeg.is_none() =>
            {
                match Decoder::open(&path).and_then(|decoder| {
                    let mut track =
                        Track::new(&decoder, output.device.sample_rate, speed, None, 0.0)?;
                    track.normalization = gain;
                    Ok(Successor {
                        id,
                        decoder,
                        track,
                        reported: false,
                        overlap: Overlap::default(),
                    })
                }) {
                    Ok(next) => output.next = Some(next),
                    Err(_) => {
                        writeln!(stdout, "unavailable {epoch} {id}")?;
                        stdout.flush()?;
                    }
                }
            }
            Command::Accept(request_epoch, id) if request_epoch == epoch => {
                if let Some(next) = output.accept_transition(id) {
                    decoder = Box::new(next);
                    offset = 0;
                }
            }
            Command::Speed(value) => {
                speed = value;
                output.track.set_speed(value)?;
                if let Some(next) = &mut output.next {
                    next.track.set_speed(value)?;
                }
            }
            Command::Pitch(value) => {
                semitones = value;
                output.track.pitch_scale = pitch_scale(value);
            }
            Command::Volume(value) => {
                gain = value;
                output.device.set_gain(value);
            }
            Command::Normalization(value) => output.track.normalization = value,
            Command::Equalizer(settings) => {
                equalizer.configure(settings, output.device.sample_rate, output.device.channels)
            }
            Command::Observe(enabled) => tap.set_enabled(enabled),
            Command::Crossfade(milliseconds) => crossfade_ms = milliseconds,
            Command::Quit => break,
            Command::Next(..) | Command::ClearNext(..) | Command::Accept(..) => {}
        }
    }
    Ok(())
}

fn pitch_scale(semitones: f64) -> f64 {
    (semitones / 12.0).exp2()
}

struct Successor {
    id: u64,
    decoder: Decoder,
    track: Track,
    reported: bool,
    overlap: Overlap,
}

#[derive(Default)]
struct Overlap {
    outgoing: VecDeque<f32>,
    incoming: VecDeque<f32>,
    mixed_frames: u64,
}

impl Overlap {
    fn mix(
        &mut self,
        output: &mut Vec<f32>,
        frames: usize,
        channels: usize,
        fade_frames: u64,
        incoming_drained: bool,
    ) {
        output.clear();
        for _ in 0..frames {
            let blend = !self.incoming.is_empty();
            if !blend && !incoming_drained {
                break;
            }
            let progress =
                (self.mixed_frames as f32 / fade_frames.saturating_sub(1).max(1) as f32).min(1.0);
            for _ in 0..channels {
                let outgoing = self.outgoing.pop_front().unwrap();
                output.push(if blend {
                    outgoing * (1.0 - progress) + self.incoming.pop_front().unwrap() * progress
                } else {
                    outgoing
                });
            }
            self.mixed_frames += u64::from(blend);
        }
    }
}

struct Output {
    device: Device,
    track: Track,
    next: Option<Successor>,
    start_frame: u64,
    overlap_pcm: Vec<f32>,
}

impl Output {
    fn new(
        decoder: &dyn TransportSource,
        speed: f64,
        gain: f32,
        library: Option<&Path>,
        pitch: f64,
        endpoint: Option<&str>,
    ) -> Result<Self> {
        let device = Device::for_transport(endpoint)?;
        device.set_gain(gain);
        let track = Track::new(decoder, device.sample_rate, speed, library, pitch)?;
        Ok(Self {
            device,
            track,
            next: None,
            start_frame: 0,
            overlap_pcm: Vec::new(),
        })
    }

    fn seek(
        self,
        decoder: &mut dyn TransportSource,
        milliseconds: u64,
        gain: f32,
        library: Option<&Path>,
        semitones: f64,
        endpoint: Option<&str>,
    ) -> Result<(Self, u64)> {
        let Self {
            mut device,
            mut track,
            next,
            mut overlap_pcm,
            ..
        } = self;
        let previous_rate = device.sample_rate;
        // CPAL cannot flush WASAPI's queued samples. Close the old stream
        // before opening its replacement so the two endpoints never overlap.
        device.pause()?;
        drop(device);
        drop(next);
        let device = Device::for_transport(endpoint)?;
        device.set_gain(gain);
        if device.sample_rate == previous_rate {
            track.reset(decoder.samples().len())?;
        } else {
            let (speed, normalization) = (track.speed, track.normalization);
            drop(track);
            track = Track::new(decoder, device.sample_rate, speed, library, semitones)?;
            track.normalization = normalization;
        }
        overlap_pcm.clear();
        let mut output = Self {
            device,
            track,
            next: None,
            start_frame: 0,
            overlap_pcm,
        };
        if decoder.duration() != 0 && milliseconds >= decoder.duration() {
            output.track.rate.finish();
            output.device.mark_final();
            return Ok((output, decoder.duration()));
        }
        decoder.seek_to(milliseconds)?;
        Ok((output, milliseconds))
    }

    fn pump(
        &mut self,
        decoder: &mut dyn TransportSource,
        offset: u64,
        crossfade_ms: u64,
        equalizer: &mut Equalizer,
        tap: &mut Tap,
    ) -> Result<bool> {
        let available = self.device.available_frames().min(BLOCK_FRAMES);
        let worked =
            self.pump_available(decoder, offset, crossfade_ms, available, equalizer, tap)?;
        let final_block = self.track.rendering_finished()
            && self.next.as_ref().is_none_or(|next| {
                next.track.rendering_finished()
                    && next.overlap.incoming.is_empty()
                    && next.overlap.outgoing.is_empty()
            });
        self.device.set_final(final_block);
        if self.track.clock.end() != 0
            || self
                .next
                .as_ref()
                .is_some_and(|next| next.track.clock.end() != 0)
        {
            self.device.start_when_ready(final_block)?;
        }
        Ok(worked)
    }

    fn pump_available(
        &mut self,
        decoder: &mut dyn TransportSource,
        offset: u64,
        crossfade_ms: u64,
        available: usize,
        equalizer: &mut Equalizer,
        tap: &mut Tap,
    ) -> Result<bool> {
        if available == 0 {
            return Ok(false);
        }
        let channels = self.device.channels;
        let rate = u64::from(self.device.sample_rate);
        let mut enqueue = |pcm: &mut [f32]| {
            equalizer.process(pcm);
            tap.capture(pcm, channels);
            // The callback only frees slots between the capacity check and this write.
            let written = self.device.enqueue(pcm);
            debug_assert_eq!(written, pcm.len());
        };
        let Some(next) = &mut self.next else {
            return self.track.pump(decoder, available, channels, enqueue);
        };
        let overlap = &mut next.overlap;
        if self.track.rendering_finished() && overlap.outgoing.is_empty() {
            if !overlap.incoming.is_empty() {
                let count = overlap.incoming.len().min(available * channels);
                self.overlap_pcm.clear();
                self.overlap_pcm.extend(overlap.incoming.drain(..count));
                enqueue(&mut self.overlap_pcm);
                return Ok(true);
            }
            return next
                .track
                .pump(&mut next.decoder, available, channels, enqueue);
        }

        let remaining_ms = decoder.duration().saturating_sub(offset);
        let fade = (crossfade_ms
            .min(decoder.duration() / 2)
            .min(next.decoder.duration_ms / 2)
            .min(remaining_ms))
            * rate
            / 1000;
        let fade_start = (remaining_ms * rate / 1000).saturating_sub(fade);
        let until_fade = fade_start.saturating_sub(self.track.clock.end()) as usize;
        if fade == 0 || until_fade != 0 {
            let count = if fade == 0 {
                available
            } else {
                available.min(until_fade)
            };
            return self.track.pump(decoder, count, channels, enqueue);
        }

        let mut worked = false;
        if overlap.outgoing.is_empty() {
            worked |= self.track.pump(decoder, available, channels, |pcm| {
                overlap.outgoing.extend(pcm.iter().copied())
            })?;
        }
        if overlap.incoming.is_empty() && !overlap.outgoing.is_empty() {
            worked |= next.track.pump(
                &mut next.decoder,
                overlap.outgoing.len() / channels,
                channels,
                |pcm| overlap.incoming.extend(pcm.iter().copied()),
            )?;
        }
        let frames = (overlap.outgoing.len() / channels).min(available);
        overlap.mix(
            &mut self.overlap_pcm,
            frames,
            channels,
            fade,
            next.track.rendering_finished(),
        );
        if !self.overlap_pcm.is_empty() {
            enqueue(&mut self.overlap_pcm);
            worked = true;
        }
        Ok(worked)
    }

    fn report_transition(
        &mut self,
        out: &mut impl Write,
        epoch: u64,
        frames: u64,
    ) -> io::Result<bool> {
        // Hand off at the end of the predecessor, including an audible overlap.
        // The device clock excludes silence inserted to cover underruns.
        let boundary = self.start_frame + self.track.clock.end();
        let Some(next) = &mut self.next else {
            return Ok(false);
        };
        if next.reported
            || next.track.clock.end() == 0
            || frames < boundary
            || !next.overlap.incoming.is_empty()
            || !next.overlap.outgoing.is_empty()
        {
            return Ok(false);
        }
        next.reported = true;
        let duration = next.decoder.duration_ms;
        let position =
            next.track
                .position(frames - boundary + next.overlap.mixed_frames, 0, duration);
        writeln!(out, "transition {epoch} {} {duration} {position}", next.id)?;
        out.flush()?;
        Ok(true)
    }

    fn accept_transition(&mut self, id: u64) -> Option<Decoder> {
        let next = self.next.take_if(|next| next.reported && next.id == id)?;
        self.start_frame += self.track.clock.end() - next.overlap.mixed_frames;
        self.track = next.track;
        Some(next.decoder)
    }

    fn position(&mut self, frames: u64, offset: u64, duration: u64) -> u64 {
        self.track
            .position(frames.saturating_sub(self.start_frame), offset, duration)
    }

    fn drained(&self) -> bool {
        self.next.is_none()
            && self.track.rendering_finished()
            && self.track.clock.pending.is_empty()
    }

    fn report(
        &self,
        out: &mut impl Write,
        event: &str,
        epoch: u64,
        position: u64,
    ) -> io::Result<()> {
        let audible_speed = (10_000.0
            * (self.track.clock.step / self.track.base_ratio).clamp(0.25, 2.0))
        .round() as u32;
        writeln!(
            out,
            "{event} {epoch} {position} {} {audible_speed}",
            self.device.underrun_frames()
        )?;
        out.flush()
    }
}

struct Track {
    rate: LiveRate,
    pitch: Option<PitchStage>,
    speed: f64,
    normalization: f32,
    pitch_scale: f64,
    base_ratio: f64,
    source_rate: u32,
    packet_offset: usize,
    rendered: Vec<f32>,
    mixed: Vec<f32>,
    positions: Vec<f64>,
    clock: SourceClock,
}

impl Track {
    fn new(
        decoder: &dyn TransportSource,
        output_rate: u32,
        speed: f64,
        library: Option<&Path>,
        semitones: f64,
    ) -> Result<Self> {
        let (sample_rate, channels) = decoder.format();
        let base_ratio = f64::from(sample_rate) / f64::from(output_rate);
        let ramp_frames = (output_rate as usize / 40).max(1);
        let pitch_scale = pitch_scale(semitones);
        let pitch = library
            .map(|path| PitchStage::open(path, output_rate, channels, speed, pitch_scale))
            .transpose()?;
        Ok(Self {
            rate: LiveRate::new(
                base_ratio * if pitch.is_some() { 1.0 } else { speed },
                base_ratio * 2.0,
                channels,
                ramp_frames,
            )?,
            pitch,
            speed,
            normalization: 1.0,
            pitch_scale,
            base_ratio,
            source_rate: sample_rate,
            packet_offset: decoder.samples().len(),
            rendered: Vec::new(),
            mixed: Vec::new(),
            positions: Vec::new(),
            clock: SourceClock {
                step: base_ratio * speed,
                ..SourceClock::default()
            },
        })
    }

    fn set_speed(&mut self, speed: f64) -> Result<()> {
        self.speed = speed;
        if self.pitch.is_none() {
            self.rate.set_ratio(speed * self.base_ratio)?;
        }
        Ok(())
    }

    fn reset(&mut self, packet_offset: usize) -> Result<()> {
        let ratio = self.base_ratio
            * if self.pitch.is_some() {
                1.0
            } else {
                self.speed
            };
        self.rate.reset(ratio)?;
        if let Some(pitch) = &mut self.pitch {
            pitch.reset(self.speed, self.pitch_scale);
        }
        self.packet_offset = packet_offset;
        self.rendered.clear();
        self.mixed.clear();
        self.positions.clear();
        self.clock.heard = 0;
        self.clock.source = 0.0;
        self.clock.step = self.base_ratio * self.speed;
        self.clock.pending.clear();
        Ok(())
    }

    fn pump(
        &mut self,
        decoder: &mut dyn TransportSource,
        available: usize,
        output_channels: usize,
        write: impl FnOnce(&mut [f32]),
    ) -> Result<bool> {
        if available != 0 && !self.rendering_finished() {
            let (_, channels) = decoder.format();
            if self.render(available) {
                if self.packet_offset == decoder.samples().len() {
                    match decoder.poll_packet()? {
                        Packet::Data => self.packet_offset = 0,
                        Packet::Pending => return Ok(false),
                        Packet::End => {
                            self.rate.finish();
                            return Ok(true);
                        }
                    }
                }
                let samples = decoder.samples();
                let end = (self.packet_offset + 1024 * channels).min(samples.len());
                self.rate.push(&samples[self.packet_offset..end]);
                self.packet_offset = end;
                return Ok(true);
            }
            mix(
                &self.rendered,
                channels,
                output_channels,
                1.0,
                &mut self.mixed,
            );
            if self.normalization != 1.0 {
                for sample in &mut self.mixed {
                    *sample *= self.normalization;
                }
            }
            write(&mut self.mixed);
            self.clock.pending.extend(&self.positions);
        }
        Ok(available != 0 && !self.rendering_finished())
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
    fn end(&self) -> u64 {
        self.heard + self.pending.len() as u64
    }

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
    use super::*;
    use crate::media::tests::write_wav;

    #[test]
    fn crossfade_keeps_the_ramp_across_partial_device_writes() {
        let mut overlap = Overlap {
            outgoing: [0.5; 10].into(),
            incoming: [-0.5; 10].into(),
            mixed_frames: 0,
        };
        let mut block = Vec::new();
        overlap.mix(&mut block, 2, 2, 5, false);
        assert_eq!(block, [0.5, 0.5, 0.25, 0.25]);
        overlap.mix(&mut block, 3, 2, 5, false);
        assert_eq!(block, [0.0, 0.0, -0.25, -0.25, -0.5, -0.5]);
        assert_eq!(overlap.mixed_frames, 5);
    }

    #[test]
    fn split_wav_tracks_write_contiguous_pcm_at_the_device_rate() -> Result<()> {
        let samples: Vec<i16> = (-1500..1500).collect();
        let split = 1163;
        let paths = [
            write_wav("gapless-first", 48000, &samples[..split])?,
            write_wav("gapless-second", 48000, &samples[split..])?,
        ];
        let result = (|| -> Result<()> {
            let mut pcm = Vec::new();
            for (path, frames) in paths.iter().zip([split, samples.len() - split]) {
                let mut decoder = Decoder::open(path)?;
                let mut track = Track::new(&decoder, 48000, 1.0, None, 0.0)?;
                while !track.rendering_finished() {
                    track.pump(&mut decoder, 137, 2, |samples| {
                        pcm.extend_from_slice(samples)
                    })?;
                }
                assert_eq!(track.clock.end(), frames as u64);
            }
            let expected: Vec<_> = samples
                .iter()
                .flat_map(|&sample| [f32::from(sample) / 32768.0; 2])
                .collect();
            assert_eq!(pcm, expected);
            Ok(())
        })();
        for path in paths {
            std::fs::remove_file(path)?;
        }
        result
    }

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
