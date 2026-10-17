//! Python package `aurora_core`.
//!
//! `Renderer` follows `aurora_binaural.engine.Renderer`: `prepare`,
//! `set_params`, `process`, `latency_samples`, `tail_samples`, `flush`, `reset`.
//! HRTF pack paths live on the constructor because `RenderConfig` only carries
//! channel counts.

use aurora_dsp::{Params, Processor, RenderConfig, RouteB, RouteV};
use pyo3::buffer::PyBuffer;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};
use std::path::PathBuf;

enum Core {
    B(Box<RouteB>),
    V(Box<RouteV>),
}

impl Core {
    fn get(&self) -> &dyn Processor {
        match self {
            Core::B(dsp) => dsp.as_ref(),
            Core::V(dsp) => dsp.as_ref(),
        }
    }

    fn get_mut(&mut self) -> &mut dyn Processor {
        match self {
            Core::B(dsp) => dsp.as_mut(),
            Core::V(dsp) => dsp.as_mut(),
        }
    }
}

#[pyclass(name = "Renderer")]
struct PyRenderer {
    inner: Core,
    hrtf_pack: PathBuf,
    hrtf_source: PathBuf,
    preset_path: PathBuf,
    strength: f64,
    width: Option<f64>,
    height: Option<f64>,
    room: Option<f64>,
    voice_distance: Option<f64>,
    seed: u64,
    preset: String,
    limiter: bool,
    timbre_guard: bool,
    route: String,
    hrtf_name: String,
    max_block: usize,
    prepared: bool,
}

#[pymethods]
impl PyRenderer {
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (hrtf_pack, hrtf_source, preset_path, strength=0.4, seed=20_261_016, preset="room", limiter=true, timbre_guard=false, route="B", hrtf_name=""))]
    fn new(
        hrtf_pack: PathBuf,
        hrtf_source: PathBuf,
        preset_path: PathBuf,
        strength: f64,
        seed: u64,
        preset: &str,
        limiter: bool,
        timbre_guard: bool,
        route: &str,
        hrtf_name: &str,
    ) -> Self {
        let route_id = route.to_ascii_uppercase();
        let inner = if route_id == "V" {
            Core::V(Box::new(RouteV::new()))
        } else {
            Core::B(Box::new(RouteB::new()))
        };
        Self {
            inner,
            hrtf_pack,
            hrtf_source,
            preset_path,
            strength,
            width: None,
            height: None,
            room: None,
            voice_distance: None,
            seed,
            preset: preset.to_string(),
            limiter,
            timbre_guard,
            route: route_id,
            hrtf_name: hrtf_name.to_string(),
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
        let mut cfg = if self.route == "V" {
            RenderConfig::route_v(&self.hrtf_pack, &self.hrtf_source, &self.preset_path)
        } else {
            RenderConfig::route_b(&self.hrtf_pack, &self.hrtf_source, &self.preset_path)
        };
        cfg.input_channels = input_channels;
        cfg.output_channels = output_channels;
        cfg.strength = self.strength;
        cfg.seed = self.seed;
        cfg.preset = self.preset.clone();
        cfg.limiter = self.limiter;
        cfg.timbre_guard = self.timbre_guard;
        cfg.route = self.route.clone();
        cfg.hrtf_name = self.hrtf_name.clone();
        self.inner
            .get_mut()
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
        if let Some(value) = optional_f64(params, "width")? {
            self.width = Some(value);
        }
        if let Some(value) = optional_f64(params, "height")? {
            self.height = Some(value);
        }
        if let Some(value) = optional_f64(params, "room")? {
            self.room = Some(value);
        }
        if let Some(value) = optional_f64(params, "voice_distance")? {
            self.voice_distance = Some(value);
        }
        self.strength = strength;
        self.preset = preset;
        self.seed = seed;
        self.push_params();
        Ok(())
    }

    /// Update any of `strength`, `width`, `height`, `room`, `voice_distance`.
    /// Omitted keys keep their current value. Each value is in `[0, 1]`.
    fn set_controls(&mut self, controls: &Bound<'_, PyDict>) -> PyResult<()> {
        if !self.prepared {
            return Err(PyRuntimeError::new_err("prepare before set_controls"));
        }
        for key in ["strength", "width", "height", "room", "voice_distance"] {
            if let Some(value) = controls.get_item(key)? {
                let number: f64 = value.extract()?;
                if !number.is_finite() || !(0.0..=1.0).contains(&number) {
                    return Err(PyValueError::new_err(format!(
                        "{key} must be a finite number in [0, 1]"
                    )));
                }
                match key {
                    "strength" => self.strength = number,
                    "width" => self.width = Some(number),
                    "height" => self.height = Some(number),
                    "room" => self.room = Some(number),
                    "voice_distance" => self.voice_distance = Some(number),
                    _ => {}
                }
            }
        }
        self.push_params();
        Ok(())
    }

    /// Macro controls plus the internal parameters they expand to.
    fn effective_params<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let Core::V(route) = &self.inner else {
            return Err(PyRuntimeError::new_err(
                "effective_params is available on route V",
            ));
        };
        let Some(params) = route.effective_params() else {
            return Err(PyRuntimeError::new_err("prepare before effective_params"));
        };
        let dict = PyDict::new(py);
        let controls = PyDict::new(py);
        controls.set_item("strength", params.strength)?;
        controls.set_item("width", params.width)?;
        controls.set_item("height", params.height)?;
        controls.set_item("room", params.room)?;
        controls.set_item("voice_distance", params.voice_distance)?;
        dict.set_item("controls", controls)?;
        dict.set_item("front_az", params.front_az)?;
        dict.set_item("wide_db", params.wide_db)?;
        dict.set_item("surround_db", params.surround_db)?;
        dict.set_item("height_db", params.height_db)?;
        dict.set_item("height_el", params.height_el)?;
        dict.set_item("height_direct_db", params.height_direct_db)?;
        dict.set_item("ceiling_db", params.ceiling_db)?;
        dict.set_item("room_db", params.room_db)?;
        dict.set_item("early_db", params.early_db)?;
        dict.set_item("t60", params.t60)?;
        dict.set_item("direct_room_send_db", params.direct_room_send_db)?;
        dict.set_item("center_distance_m", params.center_distance_m)?;
        dict.set_item("center_spread_deg", params.center_spread_deg)?;
        dict.set_item("wide_lin", params.wide_lin)?;
        dict.set_item("surround_lin", params.surround_lin)?;
        dict.set_item("height_lin", params.height_lin)?;
        dict.set_item("early_lin", params.early_lin)?;
        dict.set_item("late_lin", params.late_lin)?;
        dict.set_item("direct_room_send_lin", params.direct_room_send_lin)?;
        dict.set_item("bypass", params.bypass)?;
        Ok(dict)
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
            .get_mut()
            .process(&[&left, &right], &mut [&mut out_l, &mut out_r], frames);
        for i in 0..frames {
            out_slice[i * 2] = out_l[i];
            out_slice[i * 2 + 1] = out_r[i];
        }
        Ok(out)
    }

    fn latency_samples(&self) -> usize {
        self.inner.get().latency_samples()
    }

    fn tail_samples(&self) -> usize {
        self.inner.get().tail_samples()
    }

    fn flush<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        if !self.prepared {
            return Err(PyRuntimeError::new_err("prepare before flush"));
        }
        let n = self.inner.get().latency_samples() + self.inner.get().tail_samples();
        let out = empty_stereo(py, n)?;
        if n == 0 {
            return Ok(out);
        }
        let output = PyBuffer::<f32>::get(&out)?;
        let out_ptr = output.buf_ptr() as *mut f32;
        let out_slice = unsafe { std::slice::from_raw_parts_mut(out_ptr, n * 2) };
        let mut out_l = vec![0.0f32; n];
        let mut out_r = vec![0.0f32; n];
        self.inner.get_mut().flush(&mut [&mut out_l, &mut out_r]);
        for i in 0..n {
            out_slice[i * 2] = out_l[i];
            out_slice[i * 2 + 1] = out_r[i];
        }
        Ok(out)
    }

    fn reset(&mut self) {
        self.inner.get_mut().reset();
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

impl PyRenderer {
    fn push_params(&mut self) {
        self.inner.get_mut().set_params(&Params {
            strength: self.strength,
            preset: self.preset.clone(),
            seed: self.seed,
            width: self.width,
            height: self.height,
            room: self.room,
            voice_distance: self.voice_distance,
        });
    }
}

fn optional_f64(obj: &Bound<'_, PyAny>, name: &str) -> PyResult<Option<f64>> {
    match obj.getattr(name) {
        Ok(value) => {
            if value.is_none() {
                return Ok(None);
            }
            Ok(Some(value.extract()?))
        }
        Err(_) => Ok(None),
    }
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
