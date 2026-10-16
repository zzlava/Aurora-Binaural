//! Strength → perceptual parameters. Endpoints come from `presets/*.toml`.
//!
//! `strength == 0` is a full bypass. Nonzero strengths follow the P0 draft
//! curve: width and side-decorrelation start at the floors documented in
//! `presets/p0_draft.toml` (48° and 2.05) and interpolate to the `*_at_1`
//! endpoints. Those floors are overridable with `width_deg_at_0` and
//! `side_decor_gain_at_0` in the same table.

use crate::error::DspError;
use std::collections::HashMap;
use std::fs;
use std::path::Path;

const DEFAULT_WIDTH_AT_0: f64 = 48.0;
const DEFAULT_SIDE_AT_0: f64 = 2.05;

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
    pub t60: f64,
    pub crossover_hz: f64,
    pub eq_mode: String,
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
                    t60: need("t60")?,
                    crossover_hz: need("crossover_hz")?,
                    eq_mode: row
                        .get("eq_mode")
                        .and_then(toml::Value::as_str)
                        .unwrap_or("frontal")
                        .to_string(),
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
        assert!((m.side_decor_gain - (2.05 + 0.4 * (2.4 - 2.05))).abs() < 1e-12);
        assert!(!m.bypass);
        assert!((m.t60 - 0.35).abs() < 1e-12);
    }
}
