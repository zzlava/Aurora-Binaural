use crate::error::DspError;
use std::path::PathBuf;

/// Channel count and asset paths passed to [`Processor::prepare`].
///
/// Mirrors `aurora_binaural.engine.RenderConfig` for channel counts, and adds
/// the HRTF pack / preset locations the route B core needs. Those paths are
/// fixed before `prepare` because the application `RenderConfig` only carries
/// channel counts.
#[derive(Debug, Clone)]
pub struct RenderConfig {
    pub input_channels: usize,
    pub output_channels: usize,
    pub hrtf_pack: PathBuf,
    pub hrtf_source: PathBuf,
    pub preset_path: PathBuf,
    pub preset: String,
    pub strength: f64,
    pub seed: u64,
    pub timbre_guard: bool,
    pub limiter: bool,
    /// Empty means infer KU100 / SADIE from the pack or SOFA path.
    pub hrtf_name: String,
    /// File length in samples. When set, the last 128 samples (or length/8)
    /// crossfade back to the dry input at the DRR gain, matching
    /// `process_file`. `None` leaves the stream endpoint alone.
    pub content_frames: Option<usize>,
}

impl RenderConfig {
    pub fn route_b(
        hrtf_pack: impl Into<PathBuf>,
        hrtf_source: impl Into<PathBuf>,
        preset_path: impl Into<PathBuf>,
    ) -> Self {
        Self {
            input_channels: 2,
            output_channels: 2,
            hrtf_pack: hrtf_pack.into(),
            hrtf_source: hrtf_source.into(),
            preset_path: preset_path.into(),
            preset: "room".to_string(),
            strength: 0.4,
            seed: 20_261_016,
            timbre_guard: false,
            limiter: true,
            hrtf_name: String::new(),
            content_frames: None,
        }
    }
}

/// Parameters updated by [`Processor::set_params`].
///
/// This is the subset of `AuroraParams` the route B core reads: strength,
/// preset id, and the explicit seed. Strength is mapped through the TOML
/// preset table; it is never a dry/wet mix.
#[derive(Debug, Clone)]
pub struct Params {
    pub strength: f64,
    pub preset: String,
    pub seed: u64,
}

/// Block processor aligned with `aurora_binaural.engine.Renderer`.
///
/// `process` and `flush` must not allocate, lock, perform IO, log, or panic.
/// `flush` writes at most `latency_samples() + tail_samples()` frames and
/// returns how many frames were written.
pub trait Processor {
    fn prepare(
        &mut self,
        sample_rate: u32,
        max_block: usize,
        config: &RenderConfig,
    ) -> Result<(), DspError>;

    fn set_params(&mut self, params: &Params);

    fn process(&mut self, input: &[&[f32]], output: &mut [&mut [f32]], n: usize);

    fn latency_samples(&self) -> usize;

    fn tail_samples(&self) -> usize;

    fn flush(&mut self, output: &mut [&mut [f32]]) -> usize;

    fn reset(&mut self);
}
