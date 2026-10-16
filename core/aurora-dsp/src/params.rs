//! Strength → perceptual parameters. Endpoints come from `presets/*.toml`.
//!
//! `strength == 0` is a full bypass. Nonzero strengths interpolate from the
//! `*_at_0` floors to the `*_at_1` endpoints. Every scalar the renderer reads
//! is required: a missing field is an error that names the field. There is
//! no numeric fallback.
//!
//! Fixed EQ gains are interpolated linearly in dB between the preset's
//! strength knots (the same rule as `strength.py`). Outside the knot range
//! the nearest endpoint row is held. This is not a nearest-neighbour pick
//! between knots.

use crate::error::DspError;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub struct FixedEq {
    pub bands_hz: Vec<f64>,
    pub strengths: Vec<f64>,
    /// HRTF id → one gain row per strength, in dB.
    pub gain_db: HashMap<String, Vec<Vec<f64>>>,
}

#[derive(Debug, Clone)]
pub struct PresetRow {
    pub id: String,
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
    /// Row-level override. Empty means the engine table is used.
    pub delays_ms: Vec<f64>,
    pub fixed_eq: Option<FixedEq>,
}

#[derive(Debug, Clone)]
pub struct PresetTable {
    pub version: i64,
    pub delays_ms: Vec<f64>,
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
        let Some(table) = value.as_table() else {
            return Err(preset_err(path, "preset file is not a table"));
        };
        let version = table
            .get("version")
            .and_then(toml::Value::as_integer)
            .ok_or_else(|| preset_err(path, "missing version"))?;
        let delays_ms = engine_delays(path, table.get("engine"))?;
        let mut rows = HashMap::new();
        for (key, item) in table {
            if key == "version" || key == "engine" {
                continue;
            }
            let Some(row) = item.as_table() else {
                return Err(preset_err(
                    path,
                    &format!("top-level '{key}' is not a preset table"),
                ));
            };
            rows.insert(key.clone(), parse_row(path, key, row, &delays_ms)?);
        }
        if rows.is_empty() {
            return Err(preset_err(path, "no presets found"));
        }
        Ok(Self {
            version,
            delays_ms,
            rows,
        })
    }

    pub fn get(&self, id: &str) -> Result<&PresetRow, DspError> {
        self.rows.get(id).ok_or_else(|| DspError::Preset {
            path: PathBuf::from(id),
            reason: format!("unknown preset '{id}'"),
        })
    }

    /// Delays for this preset: the row override, otherwise `[engine]`.
    pub fn delays_for<'a>(&'a self, row: &'a PresetRow) -> &'a [f64] {
        if row.delays_ms.is_empty() {
            &self.delays_ms
        } else {
            &row.delays_ms
        }
    }
}

impl PresetRow {
    /// Linear dB interpolation of the fixed EQ for this HRTF and strength.
    ///
    /// Knots come from `fixed_eq.strengths`. Between two knots the gain row is
    /// `(1-t)*row_i + t*row_{i+1}` with `t` the fractional strength. Strengths
    /// outside the first and last knot hold that endpoint row. A missing
    /// `fixed_eq` table, `bands_hz`, `strengths`, or `fixed_eq.<hrtf>.gain_db`
    /// is an error.
    pub fn eq_curve(&self, hrtf_key: &str, strength: f64) -> Result<(Vec<f64>, Vec<f64>), DspError> {
        let eq = self.fixed_eq.as_ref().ok_or_else(|| {
            row_err(
                &self.id,
                &format!("preset '{}' is missing fixed_eq", self.id),
            )
        })?;
        let key = canonical_hrtf_key(hrtf_key);
        let matrix = eq.gain_db.get(&key).ok_or_else(|| {
            row_err(
                &self.id,
                &format!("preset '{}' is missing fixed_eq.{key}.gain_db", self.id),
            )
        })?;
        Ok((eq.bands_hz.clone(), interp_gains(&eq.strengths, matrix, strength)))
    }
}

/// Map the names the renderer and the preset file both use onto the TOML key.
pub fn canonical_hrtf_key(name: &str) -> String {
    match name {
        "ku100" | "thk_ku100" => "thk_ku100".to_string(),
        "sadie" | "sadie_d1" | "sadie2_d1" => "sadie2_d1".to_string(),
        other => other.to_string(),
    }
}

/// Guess the preset HRTF id from the pack or SOFA path. `None` if neither
/// path mentions KU100 or SADIE.
pub fn infer_hrtf_key(pack: &Path, source: &Path) -> Option<String> {
    let blob = format!("{} {}", pack.display(), source.display()).to_ascii_lowercase();
    if blob.contains("sadie") {
        Some("sadie2_d1".to_string())
    } else if blob.contains("ku100") || blob.contains("thk") {
        Some("thk_ku100".to_string())
    } else {
        None
    }
}

fn engine_delays(path: &Path, engine: Option<&toml::Value>) -> Result<Vec<f64>, DspError> {
    let Some(table) = engine.and_then(toml::Value::as_table) else {
        return Err(preset_err(path, "missing engine"));
    };
    let delays = table
        .get("decorrelator_delays_ms")
        .and_then(toml_f64_vec)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| preset_err(path, "missing engine.decorrelator_delays_ms"))?;
    Ok(delays)
}

fn parse_row(
    path: &Path,
    id: &str,
    row: &toml::map::Map<String, toml::Value>,
    engine_delays: &[f64],
) -> Result<PresetRow, DspError> {
    let need = |name: &str| -> Result<f64, DspError> {
        row.get(name)
            .and_then(toml_number)
            .ok_or_else(|| preset_err(path, &format!("preset '{id}' is missing {name}")))
    };
    let name = row
        .get("name")
        .and_then(toml::Value::as_str)
        .ok_or_else(|| preset_err(path, &format!("preset '{id}' is missing name")))?
        .to_string();
    let eq_mode = row
        .get("eq_mode")
        .and_then(toml::Value::as_str)
        .ok_or_else(|| preset_err(path, &format!("preset '{id}' is missing eq_mode")))?
        .to_string();
    let delays_ms = match row.get("decorrelator_delays_ms") {
        Some(value) => {
            let delays = toml_f64_vec(value).filter(|v| !v.is_empty()).ok_or_else(|| {
                preset_err(
                    path,
                    &format!("preset '{id}' is missing decorrelator_delays_ms"),
                )
            })?;
            delays
        }
        None => engine_delays.to_vec(),
    };
    Ok(PresetRow {
        id: id.to_string(),
        name,
        width_deg_at_1: need("width_deg_at_1")?,
        ambient_ratio_at_1: need("ambient_ratio_at_1")?,
        early_level_at_1: need("early_level_at_1")?,
        late_level_at_1: need("late_level_at_1")?,
        drr_offset_db_at_1: need("drr_offset_db_at_1")?,
        side_decor_gain_at_1: need("side_decor_gain_at_1")?,
        width_deg_at_0: need("width_deg_at_0")?,
        side_decor_gain_at_0: need("side_decor_gain_at_0")?,
        direct_bleed_at_1: need("direct_bleed_at_1")?,
        coherence_floor: need("coherence_floor")?,
        coherence_power: need("coherence_power")?,
        pan_gamma: need("pan_gamma")?,
        wide_deg: need("wide_deg")?,
        rear_deg: need("rear_deg")?,
        hf_pan_floor: need("hf_pan_floor")?,
        hf_pan_start_hz: need("hf_pan_start_hz")?,
        hf_pan_end_hz: need("hf_pan_end_hz")?,
        hf_direct_start_hz: need("hf_direct_start_hz")?,
        hf_direct_end_hz: need("hf_direct_end_hz")?,
        bed_peak_db: need("bed_peak_db")?,
        bed_peak_lo_hz: need("bed_peak_lo_hz")?,
        bed_peak_hi_hz: need("bed_peak_hi_hz")?,
        side_lpf_hz: need("side_lpf_hz")?,
        t60: need("t60")?,
        crossover_hz: need("crossover_hz")?,
        eq_mode,
        delays_ms,
        fixed_eq: parse_fixed_eq(path, id, row.get("fixed_eq"))?,
    })
}

fn preset_err(path: &Path, reason: &str) -> DspError {
    DspError::Preset {
        path: path.to_path_buf(),
        reason: reason.to_string(),
    }
}

fn row_err(id: &str, reason: &str) -> DspError {
    DspError::Preset {
        path: PathBuf::from(id),
        reason: reason.to_string(),
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

fn parse_fixed_eq(
    path: &Path,
    id: &str,
    value: Option<&toml::Value>,
) -> Result<Option<FixedEq>, DspError> {
    let Some(value) = value else {
        return Ok(None);
    };
    let Some(table) = value.as_table() else {
        return Err(preset_err(
            path,
            &format!("preset '{id}' is missing fixed_eq"),
        ));
    };
    let bands_hz = table
        .get("bands_hz")
        .and_then(toml_f64_vec)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| preset_err(path, &format!("preset '{id}' is missing fixed_eq.bands_hz")))?;
    let strengths = table
        .get("strengths")
        .and_then(toml_f64_vec)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| preset_err(path, &format!("preset '{id}' is missing fixed_eq.strengths")))?;
    for i in 1..strengths.len() {
        if strengths[i] <= strengths[i - 1] {
            return Err(preset_err(
                path,
                &format!("preset '{id}' fixed_eq.strengths must increase"),
            ));
        }
    }
    let mut gain_db = HashMap::new();
    for (key, item) in table {
        if key == "bands_hz" || key == "strengths" {
            continue;
        }
        let Some(body) = item.as_table() else {
            return Err(preset_err(
                path,
                &format!("preset '{id}' fixed_eq.{key} is not a table"),
            ));
        };
        let rows = body.get("gain_db").and_then(toml::Value::as_array).ok_or_else(|| {
            preset_err(
                path,
                &format!("preset '{id}' is missing fixed_eq.{key}.gain_db"),
            )
        })?;
        let mut matrix = Vec::with_capacity(rows.len());
        for row in rows {
            let gains = toml_f64_vec(row).ok_or_else(|| {
                preset_err(
                    path,
                    &format!("preset '{id}' fixed_eq.{key}.gain_db is not a numeric matrix"),
                )
            })?;
            if gains.len() != bands_hz.len() {
                return Err(preset_err(
                    path,
                    &format!(
                        "preset '{id}' fixed_eq.{key}.gain_db has {} bands, expected {}",
                        gains.len(),
                        bands_hz.len()
                    ),
                ));
            }
            matrix.push(gains);
        }
        if matrix.len() != strengths.len() {
            return Err(preset_err(
                path,
                &format!(
                    "preset '{id}' fixed_eq.{key}.gain_db has {} rows, expected {} strengths",
                    matrix.len(),
                    strengths.len()
                ),
            ));
        }
        gain_db.insert(key.clone(), matrix);
    }
    if gain_db.is_empty() {
        return Err(preset_err(
            path,
            &format!("preset '{id}' is missing fixed_eq.<hrtf>.gain_db"),
        ));
    }
    Ok(Some(FixedEq {
        bands_hz,
        strengths,
        gain_db,
    }))
}

fn interp_gains(strengths: &[f64], gains: &[Vec<f64>], strength: f64) -> Vec<f64> {
    let s = strength;
    if s <= strengths[0] {
        return gains[0].clone();
    }
    if s >= strengths[strengths.len() - 1] {
        return gains[gains.len() - 1].clone();
    }
    for i in 0..strengths.len() - 1 {
        let s0 = strengths[i];
        let s1 = strengths[i + 1];
        if s0 <= s && s <= s1 {
            let span = s1 - s0;
            let t = if span <= 0.0 { 0.0 } else { (s - s0) / span };
            return gains[i]
                .iter()
                .zip(gains[i + 1].iter())
                .map(|(a, b)| (1.0 - t) * a + t * b)
                .collect();
        }
    }
    gains[gains.len() - 1].clone()
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
        assert_eq!(table.delays_ms.len(), 12);
        assert!((table.delays_ms[0] - 3.2).abs() < 1e-12);
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
        assert!((m.bed_peak_db - 0.0).abs() < 1e-12);
        assert!((m.side_lpf_hz - 6000.0).abs() < 1e-12);
        assert!(!m.bypass);
        assert!((m.t60 - 0.35).abs() < 1e-12);
        let (bands, g04) = room.eq_curve("ku100", 0.4).unwrap();
        assert_eq!(bands.len(), 26);
        assert!((g04[0] - (-0.58)).abs() < 1e-12);
        let (_, g02) = room.eq_curve("thk_ku100", 0.2).unwrap();
        let (_, g03) = room.eq_curve("ku100", 0.3).unwrap();
        assert!((g03[0] - 0.5 * (g02[0] + g04[0])).abs() < 1e-12);
        let err = room.eq_curve("missing-hrtf", 0.4).unwrap_err();
        assert!(
            err.to_string().contains("fixed_eq.missing-hrtf.gain_db"),
            "{err}"
        );
    }

    #[test]
    fn missing_field_names_the_field() {
        let text = std::fs::read_to_string(preset_path()).unwrap();
        let text = text.replace("side_lpf_hz = 6000.0\n", "");
        let path = std::env::temp_dir().join("aurora_missing_side_lpf.toml");
        std::fs::write(&path, text).unwrap();
        let err = PresetTable::load(&path).unwrap_err();
        assert!(
            err.to_string().contains("side_lpf_hz"),
            "{err}"
        );
    }
}
