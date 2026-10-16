//! Python package `aurora_core`.
//!
//! `Renderer` follows `aurora_binaural.engine.Renderer`: `prepare`,
//! `set_params`, `process`, `latency_samples`, `tail_samples`, `flush`, `reset`.
//! HRTF pack paths live on the constructor because `RenderConfig` only carries
//! channel counts.

use aurora_dsp::{Params, Processor, RenderConfig, RouteB};
use pyo3::buffer::PyBuffer;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;
use std::path::PathBuf;

#[pyclass(name = "Renderer")]
struct PyRenderer {
    inner: RouteB,
    hrtf_pack: PathBuf,
    hrtf_source: PathBuf,
    preset_path: PathBuf,
    strength: f64,
    seed: u64,
    preset: String,
    hrtf_name: Option<String>,
    limiter: bool,
    timbre_guard: bool,
    max_block: usize,
    prepared: bool,
}

#[pymethods]
impl PyRenderer {
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (hrtf_pack, hrtf_source, preset_path, strength=0.4, seed=20_261_016, preset="room", limiter=true, timbre_guard=false, hrtf_name=None))]
    fn new(
        hrtf_pack: PathBuf,
        hrtf_source: PathBuf,
        preset_path: PathBuf,
        strength: f64,
        seed: u64,
        preset: &str,
        limiter: bool,
        timbre_guard: bool,
        hrtf_name: Option<String>,
    ) -> Self {
        Self {
            inner: RouteB::new(),
            hrtf_pack,
            hrtf_source,
            preset_path,
            strength,
            seed,
            preset: preset.to_string(),
            hrtf_name,
            limiter,
            timbre_guard,
            max_block: 0,
            prepared: false,
        }
    }

    fn prepare(
        &mut self,
        sample_rate: u32,
        max_block: usize,
        config: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let input_channels = optional_usize(config, "input_channels")?.unwrap_or(2);
        let output_channels = optional_usize(config, "output_channels")?.unwrap_or(2);
        let mut cfg = RenderConfig::route_b(&self.hrtf_pack, &self.hrtf_source, &self.preset_path);
        cfg.input_channels = input_channels;
        cfg.output_channels = output_channels;
        cfg.strength = self.strength;
        cfg.seed = self.seed;
        cfg.preset = self.preset.clone();
        cfg.limiter = self.limiter;
        cfg.timbre_guard = self.timbre_guard;
        cfg.hrtf_name = self.hrtf_name.clone().unwrap_or_default();
        cfg.content_frames = optional_usize(config, "content_frames")?;
        self.inner
            .prepare(sample_rate, max_block, &cfg)
            .map_err(|err| PyRuntimeError::new_err(err.to_string()))?;
        self.max_block = max_block;
        self.prepared = true;
        Ok(())
    }

    /// Accepts an `AuroraParams`-like object: `strength` or `effective_strength()`,
    /// `preset.id` (or a string preset), and `seed`.
    fn set_params(&mut self, params: &Bound<'_, PyAny>) -> PyResult<()> {
        if !self.prepared {
            return Err(PyRuntimeError::new_err("prepare before set_params"));
        }
        let strength = read_strength(params)?;
        let preset = read_preset(params)?;
        let seed = optional_u64(params, "seed")?.unwrap_or(self.seed);
        self.strength = strength;
        self.preset = preset.clone();
        self.seed = seed;
        self.inner.set_params(&Params {
            strength,
            preset,
            seed,
        });
        Ok(())
    }

    /// `block` is float32, shape `(frames, channels)`, C-contiguous.
    /// Returns float32 `(frames, 2)`.
    fn process<'py>(
        &mut self,
        py: Python<'py>,
        block: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if !self.prepared {
            return Err(PyRuntimeError::new_err("prepare before process"));
        }
        let (frames, channels, input) = read_block(block)?;
        let out = empty_stereo(py, frames)?;
        let output = PyBuffer::<f32>::get(&out)?;
        let out_ptr = output.buf_ptr() as *mut f32;
        let out_slice = unsafe { std::slice::from_raw_parts_mut(out_ptr, frames * 2) };
        let mut left = vec![0.0f32; frames];
        let mut right = vec![0.0f32; frames];
        deinterleave(&input, frames, channels, &mut left, &mut right);
        let mut out_l = vec![0.0f32; frames];
        let mut out_r = vec![0.0f32; frames];
        self.inner
            .process(&[&left, &right], &mut [&mut out_l, &mut out_r], frames);
        for i in 0..frames {
            out_slice[i * 2] = out_l[i];
            out_slice[i * 2 + 1] = out_r[i];
        }
        Ok(out)
    }

    fn latency_samples(&self) -> usize {
        self.inner.latency_samples()
    }

    fn tail_samples(&self) -> usize {
        self.inner.tail_samples()
    }

    fn flush<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        if !self.prepared {
            return Err(PyRuntimeError::new_err("prepare before flush"));
        }
        let n = self.inner.latency_samples() + self.inner.tail_samples();
        let out = empty_stereo(py, n)?;
        if n == 0 {
            return Ok(out);
        }
        let output = PyBuffer::<f32>::get(&out)?;
        let out_ptr = output.buf_ptr() as *mut f32;
        let out_slice = unsafe { std::slice::from_raw_parts_mut(out_ptr, n * 2) };
        let mut out_l = vec![0.0f32; n];
        let mut out_r = vec![0.0f32; n];
        self.inner.flush(&mut [&mut out_l, &mut out_r]);
        for i in 0..n {
            out_slice[i * 2] = out_l[i];
            out_slice[i * 2 + 1] = out_r[i];
        }
        Ok(out)
    }

    fn reset(&mut self) {
        self.inner.reset();
    }
}

fn read_block(block: &Bound<'_, PyAny>) -> PyResult<(usize, usize, Vec<f32>)> {
    let buf = PyBuffer::<f32>::get(block)?;
    if !buf.is_c_contiguous() {
        return Err(PyValueError::new_err(
            "block must be a C-contiguous float32 array",
        ));
    }
    let shape = buf.shape();
    let (frames, channels) = match shape {
        [frames, channels] => (*frames, *channels),
        [frames] => (*frames, 1),
        _ => {
            return Err(PyValueError::new_err(
                "block must have shape (frames, channels)",
            ))
        }
    };
    if channels != 1 && channels != 2 {
        return Err(PyValueError::new_err("block must have 1 or 2 channels"));
    }
    let ptr = buf.buf_ptr() as *const f32;
    let n = frames * channels;
    let slice = unsafe { std::slice::from_raw_parts(ptr, n) };
    Ok((frames, channels, slice.to_vec()))
}

fn deinterleave(
    input: &[f32],
    frames: usize,
    channels: usize,
    left: &mut [f32],
    right: &mut [f32],
) {
    if channels == 1 {
        left[..frames].copy_from_slice(&input[..frames]);
        right[..frames].copy_from_slice(&input[..frames]);
        return;
    }
    for i in 0..frames {
        left[i] = input[i * 2];
        right[i] = input[i * 2 + 1];
    }
}

fn empty_stereo<'py>(py: Python<'py>, frames: usize) -> PyResult<Bound<'py, PyAny>> {
    let np = py.import("numpy")?;
    let dtype = np.getattr("float32")?;
    let out = np.call_method1("empty", ((frames, 2usize), dtype))?;
    Ok(out)
}

fn read_strength(params: &Bound<'_, PyAny>) -> PyResult<f64> {
    if let Ok(method) = params.getattr("effective_strength") {
        if method.is_callable() {
            return method.call0()?.extract();
        }
    }
    params.getattr("strength")?.extract()
}

fn read_preset(params: &Bound<'_, PyAny>) -> PyResult<String> {
    let preset = params.getattr("preset")?;
    if let Ok(id) = preset.getattr("id") {
        if let Ok(text) = id.extract::<String>() {
            return Ok(text);
        }
    }
    if let Ok(text) = preset.extract::<String>() {
        return Ok(text);
    }
    Ok(preset.str()?.to_string())
}

fn optional_usize(obj: &Bound<'_, PyAny>, name: &str) -> PyResult<Option<usize>> {
    match obj.getattr(name) {
        Ok(value) if value.is_none() => Ok(None),
        Ok(value) => Ok(Some(value.extract()?)),
        Err(_) => Ok(None),
    }
}

fn optional_u64(obj: &Bound<'_, PyAny>, name: &str) -> PyResult<Option<u64>> {
    match obj.getattr(name) {
        Ok(value) => Ok(Some(value.extract()?)),
        Err(_) => Ok(None),
    }
}

#[pymodule]
fn aurora_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyRenderer>()?;
    Ok(())
}
