#[derive(Debug, PartialEq)]
pub struct TimedLine {
    pub start: f64,
    pub end: Option<f64>,
    pub is_quote: bool,
    pub has_karaoke: bool,
    pub has_mora: bool,
    pub translation_rows: Vec<usize>,
    pub chunks: Vec<(f64, String)>,
}

#[derive(Debug, PartialEq)]
pub enum LrcxRecord<'a> {
    ReadingGroups(&'a str),
    LineEnds(&'a str),
    Line(TimedLine),
}

#[derive(Clone, Copy)]
struct Timestamp {
    start: usize,
    end: usize,
    seconds: f64,
}

fn numeric_prefix(input: &str) -> usize {
    input
        .chars()
        .take_while(|ch| ch.is_numeric())
        .map(char::len_utf8)
        .sum()
}

// The compatibility implementation handles non-ASCII decimal characters and
// timestamps too large to represent reliably in the native codec.
fn timestamp(input: &str, start: usize, delimiter: char) -> Option<Option<Timestamp>> {
    let rest = &input[start + 1..];
    let minutes = numeric_prefix(rest);
    let Some(after_colon) = rest[minutes..].strip_prefix(':') else {
        return Some(None);
    };
    let seconds = numeric_prefix(after_colon);
    if minutes == 0 || seconds == 0 {
        return Some(None);
    }
    let mut end = seconds;
    if let Some(fraction) = after_colon[seconds..].strip_prefix('.') {
        let decimals = numeric_prefix(fraction);
        if decimals == 0 {
            return Some(None);
        }
        end += 1 + decimals;
    }
    if !after_colon[end..].starts_with(delimiter) {
        return Some(None);
    }
    let seconds = rest[..minutes].parse::<u64>().ok()? as f64 * 60.0
        + after_colon[..end].parse::<f64>().ok()?;
    if !seconds.is_finite() || seconds >= 1e12 {
        return None;
    }
    Some(Some(Timestamp {
        start,
        end: start + minutes + end + 3,
        seconds,
    }))
}

fn tags(input: &str, open: char, close: char) -> Option<Vec<Timestamp>> {
    let mut result = Vec::new();
    for (start, _) in input.match_indices(open) {
        if let Some(tag) = timestamp(input, start, close)? {
            result.push(tag);
        }
    }
    Some(result)
}

/// Expand adjacent lines that share the same timestamp sequence in occurrence order.
pub fn parse_lrc<'a>(lines: impl IntoIterator<Item = &'a str>) -> Option<Vec<(f64, &'a str)>> {
    let mut physical = Vec::new();
    for line in lines {
        let line = line.trim_end_matches(['\r', '\n']);
        let timestamps = tags(line, '[', ']')?;
        if let Some(last) = timestamps.last() {
            let text = line[last.end..].trim_start_matches(|ch: char| {
                ch.is_whitespace() || ('\x1c'..='\x1f').contains(&ch)
            });
            physical.push((
                timestamps
                    .into_iter()
                    .map(|tag| tag.seconds)
                    .collect::<Vec<_>>(),
                text,
            ));
        }
    }
    let mut lines = Vec::new();
    for group in physical.chunk_by(|left, right| left.0 == right.0) {
        for &start in &group[0].0 {
            lines.extend(group.iter().map(|(_, text)| (start, *text)));
        }
    }
    lines.sort_by(|(left, _), (right, _)| left.total_cmp(right));
    Some(lines)
}

fn decode_breaks(content: &str) -> (String, Vec<usize>) {
    let mut result = String::with_capacity(content.len());
    let mut translations = Vec::new();
    let mut row = 0;
    let mut remaining = content;
    while let Some(start) = remaining.find(['{', '\n']) {
        result.push_str(&remaining[..start]);
        remaining = &remaining[start..];
        let (len, translation) = if remaining.starts_with(r"{\nt}") {
            (5, true)
        } else if remaining.starts_with(r"{\n}") {
            (4, false)
        } else if remaining.starts_with('\n') {
            (1, false)
        } else {
            result.push('{');
            remaining = &remaining[1..];
            continue;
        };
        result.push('\n');
        row += 1;
        if translation {
            translations.push(row);
        }
        remaining = &remaining[len..];
    }
    result.push_str(remaining);
    (result, translations)
}

fn karaoke_tags(content: &str) -> Option<(Vec<Timestamp>, bool)> {
    let mut timestamps = Vec::new();
    let mut mora = false;
    let mut ruby_end = 0;
    for (start, token) in content.match_indices(['{', '<']) {
        if start < ruby_end {
            continue;
        }
        if token == "<" {
            if let Some(tag) = timestamp(content, start, '>')? {
                timestamps.push(tag);
            }
        } else {
            let remainder = &content[start + 1..];
            let Some(end) = remainder.find(['{', '}']) else {
                continue;
            };
            if remainder.as_bytes()[end] != b'}' {
                continue;
            }
            if let Some((base, reading)) = remainder[..end].split_once('|') {
                if !base.is_empty() && !reading.is_empty() {
                    ruby_end = start + end + 2;
                    if !mora {
                        mora = !tags(reading, '<', '>')?.is_empty();
                    }
                }
            }
        }
    }
    Some((timestamps, mora))
}

fn is_metadata(line: &str) -> bool {
    let Some((name, _)) = line.strip_prefix('[').and_then(|body| body.split_once(':')) else {
        return false;
    };
    line.ends_with(']')
        && name.starts_with(|ch: char| ch.is_ascii_alphabetic())
        && name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

/// Identify timed lines, visual rows and word boundaries in document order.
/// Markup and authoring metadata are passed through to the editor's model.
pub fn scan_lrcx(input: &str) -> Option<Vec<LrcxRecord<'_>>> {
    let mut records = Vec::new();
    for line in input.trim_start_matches('\u{feff}').split('\n') {
        let line = line.trim_end_matches('\r');
        if let Some(value) = line
            .strip_prefix("[reading_groups:")
            .and_then(|value| value.strip_suffix(']'))
        {
            records.push(LrcxRecord::ReadingGroups(value));
            continue;
        }
        if let Some(value) = line
            .strip_prefix("[line_ends:")
            .and_then(|value| value.strip_suffix(']'))
        {
            records.push(LrcxRecord::LineEnds(value));
            continue;
        }
        if line.is_empty() || is_metadata(line) {
            continue;
        }
        let timestamps = tags(line, '[', ']')?;
        let (Some(first), Some(last)) = (timestamps.first(), timestamps.last()) else {
            continue;
        };
        let content = &line[last.end..];
        let (content, is_quote) = content
            .strip_prefix("> ")
            .map_or((content, false), |text| (text, true));
        let (content, translation_rows) = decode_breaks(content);
        let (tags, has_mora) = karaoke_tags(&content)?;
        let has_karaoke = !tags.is_empty() || has_mora || content.contains("<instant>");
        let end = tags
            .last()
            .filter(|tag| tag.end == content.len())
            .map(|tag| tag.seconds);
        let mut chunks = Vec::with_capacity(tags.len() + 1);
        let mut cursor = 0;
        let mut start = first.seconds;
        for tag in tags {
            chunks.push((start, content[cursor..tag.start].to_owned()));
            cursor = tag.end;
            start = tag.seconds;
        }
        chunks.push((start, content[cursor..].to_owned()));
        records.push(LrcxRecord::Line(TimedLine {
            start: first.seconds,
            end,
            is_quote,
            has_karaoke,
            has_mora,
            translation_rows,
            chunks,
        }));
    }
    Some(records)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lrc_keeps_occurrences_stable_and_removes_only_leading_whitespace() {
        assert_eq!(
            parse_lrc([
                "[00:03][00:01] Text ",
                "[00:03][00:01](reading)",
                "[00:02] Other"
            ]),
            Some(vec![
                (1.0, "Text "),
                (1.0, "(reading)"),
                (2.0, "Other"),
                (3.0, "Text "),
                (3.0, "(reading)")
            ])
        );
        assert_eq!(parse_lrc(["[００:０１]unicode timestamp"]), None);
    }

    #[test]
    fn lrcx_tracks_pauses_and_keeps_mora_tags_inside_ruby() {
        let records = scan_lrcx(r"[00:01.000]> **{穴|あ<00:01.500>な}**<00:02.000><00:04.000>{\nt}Translated<00:05.000>").unwrap();
        let [LrcxRecord::Line(line)] = &records[..] else {
            panic!("expected one timed line")
        };
        assert!(line.has_karaoke && line.has_mora && line.is_quote);
        assert_eq!(line.translation_rows, [1]);
        assert_eq!(line.end, Some(5.0));
        assert_eq!(
            line.chunks,
            [
                (1.0, "**{穴|あ<00:01.500>な}**".into()),
                (2.0, String::new()),
                (4.0, "\nTranslated".into()),
                (5.0, String::new())
            ]
        );
    }
}
