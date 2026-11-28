use std::fmt::Write;

pub struct PreparedSegment<'a> {
    pub start: f64,
    pub end: Option<f64>,
    pub markup: &'a str,
    pub instant: bool,
    pub is_break: bool,
    pub source_is_newline: bool,
    pub has_mora: bool,
    pub empty: bool,
}

pub struct PreparedLine<'a> {
    pub start: f64,
    pub end: Option<f64>,
    pub is_quote: bool,
    pub has_karaoke: bool,
    pub segments: Vec<PreparedSegment<'a>>,
}

fn timestamp(output: &mut String, seconds: f64, open: char, close: char) {
    let positive = seconds.max(0.0);
    // Match Python float floor division at values just below a minute boundary.
    let minutes = ((positive - positive % 60.0) / 60.0).round() as u64;
    let remainder = positive - (minutes * 60) as f64;
    let _ = write!(output, "{open}{minutes:02}:{remainder:06.3}{close}");
}

pub fn write_lrc<'a>(header: &str, lines: impl IntoIterator<Item = (f64, &'a str)>) -> String {
    let mut output = String::from(header);
    for (start, text) in lines {
        timestamp(&mut output, start, '[', ']');
        output.push_str(&text.replace('\n', " "));
        output.push('\n');
    }
    output
}

/// Write a timed line using markup prepared in the editor's text coordinates.
pub fn write_lrcx(output: &mut String, line: &PreparedLine<'_>) {
    timestamp(output, line.start, '[', ']');
    if line.is_quote {
        output.push_str("> ");
    }
    let body = output.len();
    let mut active = line.start;
    let mut marker = false;

    for (index, segment) in line.segments.iter().enumerate() {
        if segment.empty {
            continue;
        }
        marker |= segment.has_mora;
        if segment.is_break {
            output.push_str(segment.markup);
            continue;
        }
        if segment.start != active || (line.has_karaoke && index != 0) {
            timestamp(output, segment.start, '<', '>');
            active = segment.start;
            marker = true;
        }
        if segment.instant {
            output.push_str("<instant>");
            marker = true;
        }
        output.push_str(segment.markup);

        if line.has_karaoke {
            if let Some(end) = segment.end.filter(|&end| end >= segment.start) {
                let next = line.segments.get(index + 1);
                if next.is_some_and(|next| next.source_is_newline) {
                    // Keep the end boundary empty before the structural break.
                    timestamp(output, end, '<', '>');
                    timestamp(output, end, '<', '>');
                    active = end;
                    marker = true;
                } else if end != next.map_or(active, |next| next.start) {
                    timestamp(output, end, '<', '>');
                    active = end;
                    marker = true;
                }
            }
        }
    }

    if line.has_karaoke {
        if let Some(end) = line.end.filter(|&end| {
            line.segments
                .last()
                .is_none_or(|segment| segment.end != Some(end) || segment.start == end)
        }) {
            timestamp(output, end, '<', '>');
            marker = true;
        }
        if !marker {
            let mut tag = String::new();
            timestamp(&mut tag, line.start, '<', '>');
            output.insert_str(body, &tag);
        }
    }
    output.push('\n');
}

#[cfg(test)]
mod tests {
    use super::*;

    fn segment(start: f64, markup: &str) -> PreparedSegment<'_> {
        PreparedSegment {
            start,
            end: None,
            markup,
            instant: false,
            is_break: false,
            source_is_newline: false,
            has_mora: false,
            empty: false,
        }
    }

    #[test]
    fn keeps_equal_starts_and_explicit_ends_around_a_visual_break() {
        let first = PreparedSegment {
            end: Some(1.5),
            ..segment(1.0, "{穴|あな}")
        };
        let newline = PreparedSegment {
            is_break: true,
            source_is_newline: true,
            ..segment(1.0, r"{\nt}")
        };
        let line = PreparedLine {
            start: 1.0,
            end: Some(3.0),
            is_quote: true,
            has_karaoke: true,
            segments: vec![
                first,
                newline,
                segment(2.0, "**Translation**"),
                segment(2.0, " more"),
            ],
        };
        let mut output = String::new();
        write_lrcx(&mut output, &line);
        assert_eq!(
            output,
            concat!(
                r"[00:01.000]> {穴|あな}<00:01.500><00:01.500>{\nt}",
                "<00:02.000>**Translation**<00:02.000> more<00:03.000>\n"
            )
        );
    }
}
