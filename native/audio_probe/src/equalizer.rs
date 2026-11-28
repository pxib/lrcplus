use std::f64::consts::TAU;

#[derive(Clone, Copy)]
enum Shape {
    Bell,
    LowShelf,
    HighShelf,
}

#[derive(Clone, Copy)]
struct Band {
    shape: Shape,
    frequency: f64,
    gain: f64,
    q: f64,
}

#[derive(Default)]
pub struct Settings {
    preamp: f64,
    bands: Vec<Band>,
}

impl Settings {
    pub fn parse<'a>(fields: impl Iterator<Item = &'a str>) -> Option<Self> {
        let mut fields = fields.peekable();
        let preamp = number(fields.next()?, -24.0, 0.0)?;
        let mut bands = Vec::new();
        while let Some(shape) = fields.next() {
            if bands.len() == 31 {
                return None;
            }
            let shape = match shape {
                "bell" => Shape::Bell,
                "low_shelf" => Shape::LowShelf,
                "high_shelf" => Shape::HighShelf,
                _ => return None,
            };
            let frequency = number(fields.next()?, 1.0, 24_000.0)?;
            let gain = number(fields.next()?, -24.0, 24.0)?;
            let q = number(fields.next()?, 0.05, 24.0)?;
            bands.push(Band {
                shape,
                frequency,
                gain,
                q,
            });
        }
        Some(Self { preamp, bands })
    }
}

fn number(field: &str, low: f64, high: f64) -> Option<f64> {
    let value = field.parse().ok()?;
    (low..=high).contains(&value).then_some(value)
}

struct Filter {
    coefficients: [f64; 5],
    delay: Vec<[f64; 2]>,
}

impl Filter {
    fn new(band: &Band, rate: u32, channels: usize) -> Self {
        let a = 10.0_f64.powf(band.gain / 40.0);
        let (sin, cos) = (TAU * band.frequency / f64::from(rate)).sin_cos();
        let alpha = sin / (2.0 * band.q);
        let slope = 2.0 * alpha * a.sqrt();
        let sum = a + 1.0;
        let diff = a - 1.0;
        let (b0, b1, b2, a0, a1, a2) = match band.shape {
            Shape::Bell => (
                1.0 + alpha * a,
                -2.0 * cos,
                1.0 - alpha * a,
                1.0 + alpha / a,
                -2.0 * cos,
                1.0 - alpha / a,
            ),
            Shape::LowShelf => (
                a * (sum - diff * cos + slope),
                2.0 * a * (diff - sum * cos),
                a * (sum - diff * cos - slope),
                sum + diff * cos + slope,
                -2.0 * (diff + sum * cos),
                sum + diff * cos - slope,
            ),
            Shape::HighShelf => (
                a * (sum + diff * cos + slope),
                -2.0 * a * (diff + sum * cos),
                a * (sum + diff * cos - slope),
                sum - diff * cos + slope,
                2.0 * (diff - sum * cos),
                sum - diff * cos - slope,
            ),
        };
        Self {
            coefficients: [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0],
            delay: vec![[0.0; 2]; channels],
        }
    }

    fn process(&mut self, channel: usize, input: f64) -> f64 {
        let [b0, b1, b2, a1, a2] = self.coefficients;
        let [first, second] = &mut self.delay[channel];
        let output = b0 * input + *first;
        *first = b1 * input - a1 * output + *second;
        *second = b2 * input - a2 * output;
        output
    }
}

#[derive(Default)]
pub struct Equalizer {
    settings: Settings,
    filters: Vec<Filter>,
    channels: usize,
}

impl Equalizer {
    pub fn configure(&mut self, settings: Settings, rate: u32, channels: usize) {
        self.settings = settings;
        self.reset(rate, channels);
    }

    pub fn reset(&mut self, rate: u32, channels: usize) {
        self.channels = channels;
        self.filters = self
            .settings
            .bands
            .iter()
            .filter(|band| band.gain != 0.0 && band.frequency < f64::from(rate) / 2.0)
            .map(|band| Filter::new(band, rate, channels))
            .collect();
    }

    pub fn process(&mut self, samples: &mut [f32]) {
        if self.filters.is_empty() && self.settings.preamp == 0.0 {
            return;
        }
        let preamp = 10.0_f64.powf(self.settings.preamp / 20.0);
        for frame in samples.chunks_exact_mut(self.channels) {
            for (channel, output) in frame.iter_mut().enumerate() {
                let mut sample = f64::from(*output) * preamp;
                for filter in &mut self.filters {
                    sample = filter.process(channel, sample);
                }
                *output = sample as f32;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn boosts_the_center_frequency_without_crossing_channels() {
        let mut eq = Equalizer::default();
        eq.configure(
            Settings::parse("-3 bell 1000 9 1.4".split_whitespace()).unwrap(),
            48000,
            2,
        );
        let mut samples: Vec<f32> = (0..4800)
            .flat_map(|i| [(0.2 * (TAU * f64::from(i) / 48.0).sin()) as f32, 0.0])
            .collect();
        for chunk in samples.chunks_mut(166) {
            eq.process(chunk);
        }
        assert!(samples
            .iter()
            .skip(1)
            .step_by(2)
            .all(|sample| *sample == 0.0));
        let peak = samples[4800..]
            .iter()
            .fold(0.0_f32, |peak, &sample| peak.max(sample.abs()));
        assert!((peak - 0.2 * 10.0_f32.powf(6.0 / 20.0)).abs() < 0.001);
    }
}
