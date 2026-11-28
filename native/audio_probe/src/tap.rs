use std::io::{self, Write};
use std::sync::mpsc::{self, SyncSender};
use std::thread;

const MAX_FRAMES: usize = 4096;

#[derive(Default)]
pub struct Tap {
    enabled: bool,
    samples: Vec<f32>,
    sender: Option<SyncSender<Snapshot>>,
}

struct Snapshot {
    epoch: u64,
    rate: u32,
    channels: usize,
    samples: Vec<f32>,
}

impl Tap {
    pub fn set_enabled(&mut self, enabled: bool) {
        if self.enabled == enabled {
            return;
        }
        self.enabled = enabled;
        self.clear();
        if enabled && self.sender.is_none() {
            let (send, read) = mpsc::sync_channel::<Snapshot>(1);
            self.sender = Some(send);
            thread::spawn(move || {
                // A busy GUI may stop reading the pipe. The audio transport can
                // drop snapshots while this writer waits for room.
                let hex = b"0123456789abcdef";
                let mut line = Vec::new();
                while let Ok(snapshot) = read.recv() {
                    line.clear();
                    let _ = write!(
                        line,
                        "pcm {} {} {} ",
                        snapshot.epoch, snapshot.rate, snapshot.channels
                    );
                    for sample in snapshot.samples {
                        for byte in sample.to_le_bytes() {
                            line.extend([hex[usize::from(byte >> 4)], hex[usize::from(byte & 15)]]);
                        }
                    }
                    line.push(b'\n');
                    if io::stderr().lock().write_all(&line).is_err() {
                        break;
                    }
                }
            });
        }
    }

    pub fn clear(&mut self) {
        self.samples.clear();
    }

    pub fn capture(&mut self, samples: &[f32], channels: usize) {
        if !self.enabled {
            return;
        }
        let observed = channels.min(2);
        for frame in samples
            .chunks_exact(channels)
            .take(MAX_FRAMES.saturating_sub(self.samples.len() / observed))
        {
            self.samples.extend_from_slice(&frame[..observed]);
        }
    }

    pub fn report(&mut self, epoch: u64, rate: u32, channels: usize) {
        if !self.samples.is_empty() {
            if let Some(sender) = &self.sender {
                let _ = sender.try_send(Snapshot {
                    epoch,
                    rate,
                    channels: channels.min(2),
                    samples: std::mem::take(&mut self.samples),
                });
            }
        }
    }
}
