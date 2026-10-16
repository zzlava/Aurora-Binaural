use std::path::PathBuf;

/// Errors from `prepare`, pack loading, and preset parsing.
///
/// `process` does not return this type: a prepared processor treats bad
/// samples deterministically and does not panic.
#[derive(Debug, thiserror::Error)]
pub enum DspError {
    #[error("sample rate {0} is not supported; the route B core runs at 48000 Hz")]
    SampleRate(u32),
    #[error("max_block must be positive, got {0}")]
    BlockSize(usize),
    #[error("input channels must be 1 or 2, got {0}")]
    InputChannels(usize),
    #[error("output channels must be 2, got {0}")]
    OutputChannels(usize),
    #[error("processor used before prepare")]
    NotPrepared,
    #[error("failed to read {path}: {source}")]
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
    #[error("HRIR pack {path}: {reason}")]
    Pack { path: PathBuf, reason: String },
    #[error(
        "HRIR source hash mismatch for {file}: pack records {expected}, file is {actual}. Refusing to fall back to other HRTF data"
    )]
    HashMismatch {
        file: PathBuf,
        expected: String,
        actual: String,
    },
    #[error("preset file {path}: {reason}")]
    Preset { path: PathBuf, reason: String },
    #[error("{0}")]
    Message(String),
}
