mod parse;
mod write;

pub use parse::{parse_lrc, scan_lrcx, LrcxRecord, TimedLine};
pub use write::{write_lrc, write_lrcx, PreparedLine, PreparedSegment};

use std::{slice, str};

const MAX_INPUT: usize = 64 * 1024 * 1024;

#[repr(C)]
pub struct Buffer {
    data: *mut u8,
    len: usize,
}

struct Reader<'a>(&'a [u8]);

impl<'a> Reader<'a> {
    fn take(&mut self, len: usize) -> Option<&'a [u8]> {
        let (data, rest) = self.0.split_at_checked(len)?;
        self.0 = rest;
        Some(data)
    }

    fn count(&mut self) -> Option<usize> {
        Some(u32::from_le_bytes(self.take(4)?.try_into().ok()?) as usize)
    }

    fn text(&mut self) -> Option<&'a str> {
        let len = self.count()?;
        str::from_utf8(self.take(len)?).ok()
    }

    fn time(&mut self) -> Option<f64> {
        let value = f64::from_le_bytes(self.take(8)?.try_into().ok()?);
        (value.is_finite() && value.abs() < 1e12).then_some(value)
    }

    fn optional_time(&mut self) -> Option<Option<f64>> {
        if self.take(1)?[0] == 0 {
            Some(None)
        } else {
            self.time().map(Some)
        }
    }

    fn flags(&mut self) -> Option<u8> {
        Some(self.take(1)?[0])
    }
}

fn count(output: &mut Vec<u8>, value: usize) -> Option<()> {
    output.extend(u32::try_from(value).ok()?.to_le_bytes());
    Some(())
}

fn text(output: &mut Vec<u8>, value: &str) -> Option<()> {
    count(output, value.len())?;
    output.extend(value.as_bytes());
    Some(())
}

fn optional_time(output: &mut Vec<u8>, value: Option<f64>) {
    output.push(u8::from(value.is_some()));
    if let Some(value) = value {
        output.extend(value.to_le_bytes());
    }
}

fn transform(operation: u8, data: &[u8]) -> Option<Vec<u8>> {
    let mut input = Reader(data);
    let mut output = Vec::new();
    match operation {
        0 => {
            let lines = (0..input.count()?)
                .map(|_| input.text())
                .collect::<Option<Vec<_>>>()?;
            let lines = parse_lrc(lines)?;
            count(&mut output, lines.len())?;
            for (start, lyric) in lines {
                output.extend(start.to_le_bytes());
                text(&mut output, lyric)?;
            }
        }
        1 => {
            let records = scan_lrcx(input.text()?)?;
            count(&mut output, records.len())?;
            for record in records {
                match record {
                    LrcxRecord::ReadingGroups(value) | LrcxRecord::LineEnds(value) => {
                        output.push(if matches!(record, LrcxRecord::ReadingGroups(_)) {
                            0
                        } else {
                            1
                        });
                        text(&mut output, value)?;
                    }
                    LrcxRecord::Line(line) => {
                        output.push(2);
                        output.extend(line.start.to_le_bytes());
                        output.push(
                            u8::from(line.is_quote)
                                | (u8::from(line.has_karaoke) << 1)
                                | (u8::from(line.has_mora) << 2),
                        );
                        optional_time(&mut output, line.end);
                        count(&mut output, line.translation_rows.len())?;
                        for row in line.translation_rows {
                            count(&mut output, row)?;
                        }
                        count(&mut output, line.chunks.len())?;
                        for (start, chunk) in line.chunks {
                            output.extend(start.to_le_bytes());
                            text(&mut output, &chunk)?;
                        }
                    }
                }
            }
        }
        2 => {
            let header = input.text()?;
            let lines = (0..input.count()?)
                .map(|_| Some((input.time()?, input.text()?)))
                .collect::<Option<Vec<_>>>()?;
            output = write_lrc(header, lines).into_bytes();
        }
        3 => {
            let header = input.text()?;
            let mut result = String::from(header);
            for _ in 0..input.count()? {
                let start = input.time()?;
                let end = input.optional_time()?;
                let flags = input.flags()?;
                let segments = (0..input.count()?)
                    .map(|_| {
                        let start = input.time()?;
                        let end = input.optional_time()?;
                        let flags = input.flags()?;
                        Some(PreparedSegment {
                            start,
                            end,
                            markup: input.text()?,
                            instant: flags & 1 != 0,
                            is_break: flags & 2 != 0,
                            source_is_newline: flags & 4 != 0,
                            has_mora: flags & 8 != 0,
                            empty: flags & 16 != 0,
                        })
                    })
                    .collect::<Option<Vec<_>>>()?;
                write_lrcx(
                    &mut result,
                    &PreparedLine {
                        start,
                        end,
                        is_quote: flags & 1 != 0,
                        has_karaoke: flags & 2 != 0,
                        segments,
                    },
                );
            }
            output = result.into_bytes();
        }
        _ => return None,
    }
    input.0.is_empty().then_some(output)
}

/// Transform one UTF-8 document or a length-prefixed batch of lyric rows.
///
/// # Safety
/// `data` must address `len` readable bytes and `out` must be writable.
/// On success the caller must return the buffer with `lrcplus_lyrics_free_v1`.
#[no_mangle]
pub unsafe extern "C" fn lrcplus_lyrics_transform_v1(
    operation: u8,
    data: *const u8,
    len: usize,
    out: *mut Buffer,
) -> bool {
    if data.is_null() || out.is_null() || len > MAX_INPUT {
        return false;
    }
    let Some(output) = transform(operation, unsafe { slice::from_raw_parts(data, len) }) else {
        return false;
    };
    let len = output.len();
    let data = Box::into_raw(output.into_boxed_slice()).cast::<u8>();
    unsafe { out.write(Buffer { data, len }) };
    true
}

/// # Safety
/// The pointer and length must come from a successful transform and be freed once.
#[no_mangle]
pub unsafe extern "C" fn lrcplus_lyrics_free_v1(data: *mut u8, len: usize) {
    if !data.is_null() {
        drop(unsafe { Box::from_raw(std::ptr::slice_from_raw_parts_mut(data, len)) });
    }
}
