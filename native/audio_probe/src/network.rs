use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::thread;
use std::time::{Duration, Instant};

use crate::media::{Packet, TransportSource};
use crate::{Options, Result};

const SAMPLE_RATE: u32 = 48000;
const CHANNELS: usize = 2;
const FRAME_BYTES: usize = CHANNELS * size_of::<f32>();
const PROTOCOLS: &str = "http,https,tcp,tls,crypto,httpproxy";

pub struct NetworkSource {
    child: Decoder,
    input: Receiver<Vec<f32>>,
    current: Vec<f32>,
    program: PathBuf,
    address: PathBuf,
    duration: u64,
}

impl NetworkSource {
    pub fn open(options: &Options) -> Result<Self> {
        let program = options
            .stream_ffmpeg
            .as_ref()
            .ok_or("FFmpeg is unavailable")?;
        let duration = options
            .stream_ffprobe
            .as_ref()
            .and_then(|probe| duration(probe, &options.path))
            .unwrap_or(0);
        let (child, input) = Decoder::open(program, &options.path, 0)?;
        Ok(Self {
            child,
            input,
            current: Vec::new(),
            program: program.clone(),
            address: options.path.clone(),
            duration,
        })
    }
}

impl TransportSource for NetworkSource {
    fn format(&self) -> (u32, usize) {
        (SAMPLE_RATE, CHANNELS)
    }
    fn duration(&self) -> u64 {
        self.duration
    }
    fn samples(&self) -> &[f32] {
        &self.current
    }

    fn poll_packet(&mut self) -> Result<Packet> {
        match self.input.try_recv() {
            Ok(samples) => {
                self.current = samples;
                Ok(Packet::Data)
            }
            Err(TryRecvError::Empty) => Ok(Packet::Pending),
            Err(TryRecvError::Disconnected) if self.child.0.wait()?.success() => Ok(Packet::End),
            Err(TryRecvError::Disconnected) => {
                Err("the network audio stream could not be decoded".into())
            }
        }
    }

    fn seek_to(&mut self, milliseconds: u64) -> Result<()> {
        if self.duration == 0 {
            return Err("cannot seek a live stream".into());
        }
        self.child.stop();
        let (child, input) = Decoder::open(&self.program, &self.address, milliseconds)?;
        self.child = child;
        self.input = input;
        // Track::pump will poll again after the old packet's cursor reaches its end.
        Ok(())
    }
}

fn child_command(path: &Path) -> Command {
    let mut command = Command::new(path);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    command
}

fn duration(probe: &Path, address: &Path) -> Option<u64> {
    let mut child = Decoder(
        child_command(probe)
            .args([
                "-v",
                "error",
                "-rw_timeout",
                "2500000",
                "-protocol_whitelist",
                PROTOCOLS,
                "-analyzeduration",
                "500000",
                "-probesize",
                "65536",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
            ])
            .arg(address)
            .spawn()
            .ok()?,
    );
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        if let Some(status) = child.0.try_wait().ok()? {
            if !status.success() {
                return None;
            }
            break;
        }
        if Instant::now() >= deadline {
            return None;
        }
        thread::sleep(Duration::from_millis(10));
    }
    let mut result = String::new();
    child
        .0
        .stdout
        .take()?
        .take(64)
        .read_to_string(&mut result)
        .ok()?;
    let seconds = result.trim().parse::<f64>().ok()?;
    (seconds.is_finite() && seconds > 0.0).then(|| (seconds * 1000.0).round() as u64)
}

struct Decoder(Child);

impl Decoder {
    fn stop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }

    fn open(program: &Path, address: &Path, position: u64) -> Result<(Self, Receiver<Vec<f32>>)> {
        let mut command = child_command(program);
        command.args([
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-rw_timeout",
            "5000000",
            "-protocol_whitelist",
            PROTOCOLS,
            "-analyzeduration",
            "500000",
            "-probesize",
            "65536",
        ]);
        if position != 0 {
            command.args([
                "-ss",
                &format!("{}.{:03}", position / 1000, position % 1000),
            ]);
        }
        command.arg("-i").arg(address).args([
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-f",
            "f32le",
            "-ac",
            "2",
            "-ar",
            &SAMPLE_RATE.to_string(),
            "pipe:1",
        ]);
        let mut child = Self(command.spawn()?);
        let mut reader = child
            .0
            .stdout
            .take()
            .ok_or("could not open the network decoder pipe")?;
        let (send, receive) = mpsc::sync_channel(6);
        thread::spawn(move || {
            let mut bytes = [0; 1024 * FRAME_BYTES];
            loop {
                let mut read = 0;
                while read < bytes.len() {
                    match reader.read(&mut bytes[read..]) {
                        Ok(0) => break,
                        Ok(count) => read += count,
                        Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                        Err(_) => return,
                    }
                }
                if read < FRAME_BYTES {
                    return;
                }
                let samples = bytes[..read - read % FRAME_BYTES]
                    .chunks_exact(size_of::<f32>())
                    .map(|sample| f32::from_le_bytes(sample.try_into().unwrap()))
                    .collect();
                if send.send(samples).is_err() || read < bytes.len() {
                    return;
                }
            }
        });
        Ok((child, receive))
    }
}

impl Drop for Decoder {
    fn drop(&mut self) {
        self.stop();
    }
}
