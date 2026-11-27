use std::fs::File;
use std::io::ErrorKind;
use std::path::Path;

use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{Decoder as Codec, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error;
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use symphonia::core::units::{Time, TimeBase};

use crate::Result;

pub struct Decoder {
    reader: Box<dyn FormatReader>,
    codec: Box<dyn Codec>,
    track: u32,
    samples: Option<SampleBuffer<f32>>,
    prefetched: bool,
    time_base: TimeBase,
    start_ts: u64,
    seek_target: Option<u64>,
    skip_samples: usize,
    pub duration_ms: u64,
    pub frame_count_hint: u64,
    pub sample_rate: u32,
    pub channels: usize,
}

impl Decoder {
    pub fn open(path: &Path) -> Result<Self> {
        let stream = MediaSourceStream::new(Box::new(File::open(path)?), Default::default());
        let mut hint = Hint::new();
        if let Some(extension) = path.extension().and_then(|extension| extension.to_str()) {
            hint.with_extension(extension);
        }
        let reader = symphonia::default::get_probe()
            .format(
                &hint,
                stream,
                &FormatOptions {
                    enable_gapless: true,
                    ..Default::default()
                },
                &MetadataOptions::default(),
            )?
            .format;
        let track = reader
            .default_track()
            .filter(|track| track.codec_params.codec != CODEC_TYPE_NULL)
            .or_else(|| {
                reader
                    .tracks()
                    .iter()
                    .find(|track| track.codec_params.codec != CODEC_TYPE_NULL)
            })
            .ok_or("no supported audio track")?;
        let codec = symphonia::default::get_codecs()
            .make(&track.codec_params, &DecoderOptions::default())?;
        let params = &track.codec_params;
        let time_base = params.time_base;
        let start_ts = params.start_ts;
        let duration = params.n_frames;
        let track = track.id;
        let mut decoder = Self {
            reader,
            codec,
            track,
            samples: None,
            prefetched: false,
            time_base: TimeBase::new(1, 1),
            start_ts,
            seek_target: None,
            skip_samples: 0,
            duration_ms: 0,
            frame_count_hint: 0,
            sample_rate: 0,
            channels: 0,
        };
        // Some containers leave the real channel layout to the codec headers.
        // Keep the first decoded packet so probing never consumes audible data.
        decoder.prefetched = decoder.read_packet()?;
        if !decoder.prefetched {
            return Err("no audio frames decoded".into());
        }
        decoder.time_base = time_base.unwrap_or_else(|| TimeBase::new(1, decoder.sample_rate));
        if let Some(duration) = duration {
            let seconds = decoder.seconds(duration);
            decoder.duration_ms = (seconds * 1000.0).round() as u64;
            decoder.frame_count_hint = (seconds * f64::from(decoder.sample_rate)).round() as u64;
        }
        Ok(decoder)
    }

    pub fn seek(&mut self, milliseconds: u64) -> Result<()> {
        let time = Time::new(milliseconds / 1000, (milliseconds % 1000) as f64 / 1000.0);
        let ts = self
            .start_ts
            .saturating_add(self.time_base.calc_timestamp(time));
        let sought = self.reader.seek(
            SeekMode::Accurate,
            SeekTo::TimeStamp {
                ts,
                track_id: self.track,
            },
        )?;
        self.codec.reset();
        self.prefetched = false;
        self.seek_target = Some(sought.required_ts);
        Ok(())
    }

    fn seconds(&self, timestamp: u64) -> f64 {
        let time = self.time_base.calc_time(timestamp);
        time.seconds as f64 + time.frac
    }

    pub fn next(&mut self) -> Result<Option<&[f32]>> {
        if self.prefetched {
            self.prefetched = false;
        } else if !self.read_packet()? {
            return Ok(None);
        }
        Ok(Some(self.current_samples()))
    }

    pub fn current_samples(&self) -> &[f32] {
        self.samples
            .as_ref()
            .map_or(&[], |samples| &samples.samples()[self.skip_samples..])
    }

    fn read_packet(&mut self) -> Result<bool> {
        loop {
            let packet = match self.reader.next_packet() {
                Ok(packet) => packet,
                Err(Error::IoError(error)) if error.kind() == ErrorKind::UnexpectedEof => {
                    return Ok(false)
                }
                Err(error) => return Err(error.into()),
            };
            if packet.track_id() != self.track {
                continue;
            }
            let seconds_to_skip = self
                .seek_target
                .map_or(0.0, |ts| self.seconds(ts.saturating_sub(packet.ts())));
            let audio = self.codec.decode(&packet)?;
            if audio.frames() == 0 {
                continue;
            }
            let spec = *audio.spec();
            let channels = spec.channels.count();
            if self.samples.is_none() {
                if spec.rate == 0 || !(1..=2).contains(&channels) {
                    return Err("the playback prototype requires a mono or stereo source with a valid sample rate".into());
                }
                self.sample_rate = spec.rate;
                self.channels = channels;
            } else if spec.rate != self.sample_rate || channels != self.channels {
                return Err("the source changed audio format mid-track".into());
            }
            let skip = (seconds_to_skip * f64::from(spec.rate)).round() as usize;
            if skip >= audio.frames() {
                continue;
            }
            self.seek_target = None;
            self.skip_samples = skip * channels;
            let capacity = audio.capacity();
            if self
                .samples
                .as_ref()
                .is_none_or(|samples| samples.capacity() < capacity * self.channels)
            {
                self.samples = Some(SampleBuffer::new(capacity as u64, spec));
            }
            let samples = self.samples.as_mut().unwrap();
            samples.copy_interleaved_ref(audio);
            return Ok(true);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wav_seek_discards_the_samples_before_the_requested_time() -> Result<()> {
        // 80 ms of mono PCM with a distinct sample at every source frame.
        let frames = 640_i16;
        let data_size = u32::from(frames as u16) * 2;
        let mut wav = Vec::new();
        wav.extend(b"RIFF");
        wav.extend((36 + data_size).to_le_bytes());
        wav.extend(b"WAVEfmt ");
        wav.extend(16_u32.to_le_bytes());
        wav.extend(1_u16.to_le_bytes()); // PCM
        wav.extend(1_u16.to_le_bytes()); // mono
        wav.extend(8000_u32.to_le_bytes());
        wav.extend(16000_u32.to_le_bytes());
        wav.extend(2_u16.to_le_bytes()); // block size
        wav.extend(16_u16.to_le_bytes());
        wav.extend(b"data");
        wav.extend(data_size.to_le_bytes());
        for sample in 0..frames {
            wav.extend(sample.to_le_bytes());
        }
        let path = std::env::temp_dir().join(format!("lrcplus-seek-{}.wav", std::process::id()));
        std::fs::write(&path, wav)?;
        let result = (|| {
            let mut decoder = Decoder::open(&path)?;
            assert_eq!(decoder.duration_ms, 80);
            decoder.seek(31)?;
            assert_eq!(decoder.next()?.unwrap()[0], 248.0 / 32768.0);
            decoder.seek(7)?;
            assert_eq!(decoder.next()?.unwrap()[0], 56.0 / 32768.0);
            Ok(())
        })();
        std::fs::remove_file(path)?;
        result
    }
}
