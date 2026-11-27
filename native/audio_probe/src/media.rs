use std::fs::File;
use std::io::ErrorKind;
use std::path::Path;

use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{Decoder as Codec, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error;
use symphonia::core::formats::{FormatOptions, FormatReader};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

use crate::Result;

pub struct Decoder {
    reader: Box<dyn FormatReader>,
    codec: Box<dyn Codec>,
    track: u32,
    samples: Option<SampleBuffer<f32>>,
    prefetched: bool,
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
        let track = track.id;
        let mut decoder = Self {
            reader,
            codec,
            track,
            samples: None,
            prefetched: false,
            sample_rate: 0,
            channels: 0,
        };
        // Some containers leave the real channel layout to the codec headers.
        // Keep the first decoded packet so probing never consumes audible data.
        decoder.prefetched = decoder.read_packet()?;
        if !decoder.prefetched {
            return Err("no audio frames decoded".into());
        }
        Ok(decoder)
    }

    pub fn next(&mut self) -> Result<Option<&[f32]>> {
        if self.prefetched {
            self.prefetched = false;
        } else if !self.read_packet()? {
            return Ok(None);
        }
        Ok(self.samples.as_ref().map(SampleBuffer::samples))
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
