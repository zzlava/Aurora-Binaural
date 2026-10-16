//! Strength → perceptual parameters. Endpoints come from `presets/*.toml`.
//!
//! `strength == 0` is a full bypass. Nonzero strengths interpolate from the
//! `*_at_0` floors to the `*_at_1` endpoints. Width still defaults to 48°
//! when the floor is omitted. The side-feed floor is no longer the old 2.05
//! allpass gain; `side_decor_gain_at_0` in the preset is the delay-decorrelator
//! gain. Coherence, pan exponent, bleed and the fixed EQ table are read here
//! so the render chain can use the same numbers as the Python prototype.

use crate::error::DspError;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

const DEFAULT_WIDTH_AT_0: f64 = 48.0;
const DEFAULT_SIDE_AT_0: f64 = 2.05;

#[derive(Debug, Clone)]
pub struct FixedEq {
    pub bands_hz: Vec<f64>,
    pub strengths: Vec<f64>,
    /// HRTF id → one gain row per strength, in dB.
    pub gain_db: HashMap<String, Vec<Vec<f64>>>,
}

#[derive(Debug, Clone)]
pub struct PresetRow {
    pub name: String,
    pub width_deg_at_1: f64,
    pub ambient_ratio_at_1: f64,
    pub early_level_at_1: f64,
    pub late_level_at_1: f64,
    pub drr_offset_db_at_1: f64,
    pub side_decor_gain_at_1: f64,
    pub width_deg_at_0: f64,
    pub side_decor_gain_at_0: f64,
    pub direct_bleed_at_1: f64,
    pub coherence_floor: f64,
    pub coherence_power: f64,
    pub pan_gamma: f64,
    pub wide_deg: f64,
    pub rear_deg: f64,
    pub hf_pan_floor: f64,
    pub hf_pan_start_hz: f64,
    pub hf_pan_end_hz: f64,
    pub hf_direct_start_hz: f64,
    pub hf_direct_end_hz: f64,
    pub bed_peak_db: f64,
    pub bed_peak_lo_hz: f64,
    pub bed_peak_hi_hz: f64,
    pub side_lpf_hz: f64,
    pub t60: f64,
    pub crossover_hz: f64,
    pub eq_mode: String,
    pub fixed_eq: Option<FixedEq>,
}

#[derive(Debug, Clone)]
pub struct PresetTable {
    pub version: i64,
    rows: HashMap<String, PresetRow>,
}

impl PresetTable {
    pub fn load(path: &Path) -> Result<Self, DspError> {
        let text = fs::read_to_string(path).map_err(|source| DspError::Io {
            path: path.to_path_buf(),
            source,
        })?;
        let value: toml::Value = toml::from_str(&text).map_err(|err| DspError::Preset {
            path: path.to_path_buf(),
            reason: err.to_string(),
        })?;
        let version = value
            .get("version")
            .and_then(toml::Value::as_integer)
            .unwrap_or(1);
        let mut rows = HashMap::new();
        let Some(table) = value.as_table() else {
            return Err(DspError::Preset {
                path: path.to_path_buf(),
                reason: "preset file is not a table".into(),
            });
        };
        for (key, item) in table {
            if key == "version" {
                continue;
            }
            let Some(row) = item.as_table() else {
                continue;
            };
            // Top-level tables such as `[engine]` are not presets.
            if row.get("width_deg_at_1").is_none() {
                continue;
            }
            let need = |name: &str| -> Result<f64, DspError> {
                row.get(name)
                    .and_then(toml_number)
                    .ok_or_else(|| DspError::Preset {
                        path: path.to_path_buf(),
                        reason: format!("preset '{key}' is missing {name}"),
                    })
            };
            rows.insert(
                key.clone(),
                PresetRow {
                    name: row
                        .get("name")
                        .and_then(toml::Value::as_str)
                        .unwrap_or(key)
                        .to_string(),
                    width_deg_at_1: need("width_deg_at_1")?,
                    ambient_ratio_at_1: need("ambient_ratio_at_1")?,
                    early_level_at_1: need("early_level_at_1")?,
                    late_level_at_1: need("late_level_at_1")?,
                    drr_offset_db_at_1: need("drr_offset_db_at_1")?,
                    side_decor_gain_at_1: need("side_decor_gain_at_1")?,
                    width_deg_at_0: row
                        .get("width_deg_at_0")
                        .and_then(toml_number)
                        .unwrap_or(DEFAULT_WIDTH_AT_0),
                    side_decor_gain_at_0: row
                        .get("side_decor_gain_at_0")
                        .and_then(toml_number)
                        .unwrap_or(DEFAULT_SIDE_AT_0),
                    direct_bleed_at_1: row
                        .get("direct_bleed_at_1")
                        .and_then(toml_number)
                        .unwrap_or(0.0),
                    coherence_floor: row
                        .get("coherence_floor")
                        .and_then(toml_number)
                        .unwrap_or(0.22),
                    coherence_power: row
                        .get("coherence_power")
                        .and_then(toml_number)
                        .unwrap_or(2.0),
                    pan_gamma: row.get("pan_gamma").and_then(toml_number).unwrap_or(1.0),
                    wide_deg: row.get("wide_deg").and_then(toml_number).unwrap_or(90.0),
                    rear_deg: row.get("rear_deg").and_then(toml_number).unwrap_or(130.0),
                    hf_pan_floor: row.get("hf_pan_floor").and_then(toml_number).unwrap_or(1.0),
                    hf_pan_start_hz: row
                        .get("hf_pan_start_hz")
                        .and_then(toml_number)
                        .unwrap_or(3000.0),
                    hf_pan_end_hz: row
                        .get("hf_pan_end_hz")
                        .and_then(toml_number)
                        .unwrap_or(8000.0),
                    hf_direct_start_hz: row
                        .get("hf_direct_start_hz")
                        .and_then(toml_number)
                        .unwrap_or(0.0),
                    hf_direct_end_hz: row
                        .get("hf_direct_end_hz")
                        .and_then(toml_number)
                        .unwrap_or(0.0),
                    bed_peak_db: row.get("bed_peak_db").and_then(toml_number).unwrap_or(0.0),
                    bed_peak_lo_hz: row
                        .get("bed_peak_lo_hz")
                        .and_then(toml_number)
                        .unwrap_or(1000.0),
                    bed_peak_hi_hz: row
                        .get("bed_peak_hi_hz")
                        .and_then(toml_number)
                        .unwrap_or(1600.0),
                    side_lpf_hz: row.get("side_lpf_hz").and_then(toml_number).unwrap_or(0.0),
                    t60: need("t60")?,
                    crossover_hz: need("crossover_hz")?,
                    eq_mode: row
                        .get("eq_mode")
                        .and_then(toml::Value::as_str)
                        .unwrap_or("frontal")
                        .to_string(),
                    fixed_eq: parse_fixed_eq(row.get("fixed_eq")),
                },
            );
        }
        if rows.is_empty() {
            return Err(DspError::Preset {
                path: path.to_path_buf(),
                reason: "no presets found".into(),
            });
        }
        Ok(Self { version, rows })
    }

    pub fn get(&self, id: &str) -> Result<&PresetRow, DspError> {
        self.rows.get(id).ok_or_else(|| DspError::Preset {
            path: Path::new(id).to_path_buf(),
            reason: format!("unknown preset '{id}'"),
        })
    }
}

fn toml_number(value: &toml::Value) -> Option<f64> {
    value
        .as_float()
        .or_else(|| value.as_integer().map(|v| v as f64))
}

fn toml_f64_vec(value: &toml::Value) -> Option<Vec<f64>> {
    let array = value.as_array()?;
    let mut out = Vec::with_capacity(array.len());
    for item in array {
        out.push(toml_number(item)?);
    }
    Some(out)
}

fn parse_fixed_eq(value: Option<&toml::Value>) -> Option<FixedEq> {
    let table = value?.as_table()?;
    let bands_hz = table.get("bands_hz").and_then(toml_f64_vec)?;
    let strengths = table.get("strengths").and_then(toml_f64_vec)?;
    if bands_hz.is_empty() || strengths.is_empty() {
        return None;
    }
    let mut gain_db = HashMap::new();
    for (key, item) in table {
        if key == "bands_hz" || key == "strengths" {
            continue;
        }
        let Some(body) = item.as_table() else {
            continue;
        };
        let Some(rows) = body.get("gain_db").and_then(toml::Value::as_array) else {
            continue;
        };
        let mut matrix = Vec::with_capacity(rows.len());
        for row in rows {
            let Some(gains) = toml_f64_vec(row) else {
                return None;
            };
            if gains.len() != bands_hz.len() {
                return None;
            }
            matrix.push(gains);
        }
        if matrix.len() != strengths.len() {
            return None;
        }
        gain_db.insert(key.clone(), matrix);
    }
    if gain_db.is_empty() {
        return None;
    }
    Some(FixedEq {
        bands_hz,
        strengths,
        gain_db,
    })
}

/// Mapped render controls. `bypass` is the dedicated strength=0 path.
#[derive(Debug, Clone, Copy)]
pub struct MappedStrength {
    pub strength: f64,
    pub width_deg: f64,
    pub ambient_ratio: f64,
    pub early_level: f64,
    pub late_level: f64,
    pub drr_offset_db: f64,
    pub side_decor_gain: f64,
    pub bypass: bool,
    pub t60: f64,
    pub crossover_hz: f64,
    pub direct_bleed: f64,
    pub coherence_floor: f64,
    pub coherence_power: f64,
    pub pan_gamma: f64,
    pub wide_deg: f64,
    pub rear_deg: f64,
    pub hf_pan_floor: f64,
    pub hf_pan_start_hz: f64,
    pub hf_pan_end_hz: f64,
    pub hf_direct_start_hz: f64,
    pub hf_direct_end_hz: f64,
    pub bed_peak_db: f64,
    pub bed_peak_lo_hz: f64,
    pub bed_peak_hi_hz: f64,
    pub side_lpf_hz: f64,
}

pub fn map_strength(strength: f64, row: &PresetRow) -> MappedStrength {
    let s = strength.clamp(0.0, 1.0);
    if s <= 0.0 {
        return MappedStrength {
            strength: 0.0,
            width_deg: 30.0,
            ambient_ratio: 0.0,
            early_level: 0.0,
            late_level: 0.0,
            drr_offset_db: 0.0,
            side_decor_gain: 0.0,
            bypass: true,
            t60: row.t60,
            crossover_hz: row.crossover_hz,
            direct_bleed: 0.0,
            coherence_floor: row.coherence_floor,
            coherence_power: row.coherence_power,
            pan_gamma: row.pan_gamma,
            wide_deg: row.wide_deg,
            rear_deg: row.rear_deg,
            hf_pan_floor: row.hf_pan_floor,
            hf_pan_start_hz: row.hf_pan_start_hz,
            hf_pan_end_hz: row.hf_pan_end_hz,
            hf_direct_start_hz: row.hf_direct_start_hz,
            hf_direct_end_hz: row.hf_direct_end_hz,
            bed_peak_db: row.bed_peak_db,
            bed_peak_lo_hz: row.bed_peak_lo_hz,
            bed_peak_hi_hz: row.bed_peak_hi_hz,
            side_lpf_hz: row.side_lpf_hz,
        };
    }
    MappedStrength {
        strength: s,
        width_deg: row.width_deg_at_0 + s * (row.width_deg_at_1 - row.width_deg_at_0),
        ambient_ratio: s * row.ambient_ratio_at_1,
        early_level: s * row.early_level_at_1,
        late_level: s * row.late_level_at_1,
        drr_offset_db: s * row.drr_offset_db_at_1,
        side_decor_gain: row.side_decor_gain_at_0
            + s * (row.side_decor_gain_at_1 - row.side_decor_gain_at_0),
        bypass: false,
        t60: row.t60,
        crossover_hz: row.crossover_hz,
        direct_bleed: s * row.direct_bleed_at_1,
        coherence_floor: row.coherence_floor,
        coherence_power: row.coherence_power,
        pan_gamma: row.pan_gamma,
        wide_deg: row.wide_deg,
        rear_deg: row.rear_deg,
        hf_pan_floor: row.hf_pan_floor,
        hf_pan_start_hz: row.hf_pan_start_hz,
        hf_pan_end_hz: row.hf_pan_end_hz,
        hf_direct_start_hz: row.hf_direct_start_hz,
        hf_direct_end_hz: row.hf_direct_end_hz,
        bed_peak_db: row.bed_peak_db,
        bed_peak_lo_hz: row.bed_peak_lo_hz,
        bed_peak_hi_hz: row.bed_peak_hi_hz,
        side_lpf_hz: row.side_lpf_hz,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn preset_path() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../presets/p0_draft.toml")
    }

    #[test]
    fn loads_draft_and_matches_prototype_curve() {
        let table = PresetTable::load(&preset_path()).unwrap();
        assert_eq!(table.version, 1);
        let room = table.get("room").unwrap();
        let z = map_strength(0.0, room);
        assert!(z.bypass);
        assert_eq!(z.side_decor_gain, 0.0);
        let m = map_strength(0.4, room);
        assert!((m.width_deg - (48.0 + 0.4 * (80.0 - 48.0))).abs() < 1e-12);
        assert!((m.ambient_ratio - 0.4).abs() < 1e-12);
        assert!((m.early_level - 0.36).abs() < 1e-12);
        assert!((m.late_level - 0.28).abs() < 1e-12);
        assert!((m.drr_offset_db - (-2.4)).abs() < 1e-12);
        assert!((m.side_decor_gain - (0.630 + 0.4 * (1.374 - 0.630))).abs() < 1e-12);
        assert!((m.direct_bleed - 0.0).abs() < 1e-12);
        assert!((m.coherence_floor - 0.22).abs() < 1e-12);
        assert!((m.pan_gamma - 0.65).abs() < 1e-12);
        assert!((m.wide_deg - 90.0).abs() < 1e-12);
        assert!((m.hf_pan_floor - 0.0).abs() < 1e-12);
        assert!((room.hf_pan_start_hz - 3000.0).abs() < 1e-12);
        assert!((m.hf_direct_start_hz - 4000.0).abs() < 1e-12);
        assert!((m.hf_direct_end_hz - 8000.0).abs() < 1e-12);
        assert!((m.bed_peak_db - 6.0).abs() < 1e-12);
        assert!((m.side_lpf_hz - 6000.0).abs() < 1e-12);
        assert!(!m.bypass);
        assert!((m.t60 - 0.35).abs() < 1e-12);
    }
}
