//! Route V strength mapping. Every control is read from `presets/route_v.toml`.
//!
//! `[layout]` is the shared baseline. A preset table (`V1` / `V2` / `V3`)
//! only overrides what it changes, including its `fixed_eq` block. `strength`
//! scales the wide / surround / height / early / late gains and opens
//! `front_az` from 30°. It does not scale `center_distance_m` or
//! `direct_room_send_db`. `strength == 0` is a bypass.

use crate::error::DspError;
use crate::params::{parse_fixed_eq, toml_number, FixedEq};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct RouteVRow {
    pub name: String,
    pub front_az: f64,
    pub wide_az: f64,
    pub surround_az: f64,
    pub wide_db: f64,
    pub surround_db: f64,
    pub surround_delay_ms: f64,
    pub height_el: f64,
    pub height_az: Vec<f64>,
    pub height_db: f64,
    pub height_direct_db: f64,
    pub center_height_db: f64,
    pub height_rear_delay_ms: f64,
    pub direct_height_delay_ms: f64,
    pub center_distance_m: f64,
    pub center_spread_deg: f64,
    pub center_far_m: f64,
    pub direct_room_send_db: f64,
    pub early_db: f64,
    pub room_db: f64,
    pub t60: f64,
    pub ceiling_db: f64,
    pub floor_db: f64,
    pub crossover_hz: f64,
    pub coherence_floor: f64,
    pub coherence_power: f64,
    pub pan_gamma: f64,
    pub absorption: f64,
    pub early_order: i32,
    pub early_taps: usize,
    pub room_size_m: [f64; 3],
    pub listener_m: [f64; 3],
    pub source_m: [f64; 3],
    pub hrtf_ref_m: f64,
    pub near_lf_hz: f64,
    pub near_lf_max_db: f64,
    pub near_ild_scale: f64,
    /// Prototype hardcodes these on the route V upmix call. They are still
    /// read from the preset when present, so a later listen can retune them
    /// without a code change. Missing keys keep the prototype's values.
    pub direct_bleed: f64,
    pub hf_pan_floor: f64,
    pub hf_pan_start_hz: f64,
    pub hf_pan_end_hz: f64,
    pub hf_direct_start_hz: f64,
    pub hf_direct_end_hz: f64,
    pub fixed_eq: Option<FixedEq>,
}

#[derive(Debug, Clone)]
pub struct RouteVTable {
    pub version: i64,
    pub delays_ms: Vec<f64>,
    rows: HashMap<String, RouteVRow>,
}

#[derive(Debug, Clone)]
pub struct MappedRouteV {
    pub strength: f64,
    pub bypass: bool,
    pub front_az: f64,
    pub wide_az: f64,
    pub surround_az: f64,
    pub wide_lin: f64,
    pub surround_lin: f64,
    pub height_lin: f64,
    pub early_lin: f64,
    pub late_lin: f64,
    pub height_el: f64,
    pub height_az: Vec<f64>,
    pub height_direct_lin: f64,
    pub center_height_lin: f64,
    pub center_distance_m: f64,
    pub center_spread_deg: f64,
    pub direct_room_send_lin: f64,
    pub surround_delay_ms: f64,
    pub height_rear_delay_ms: f64,
    pub direct_height_delay_ms: f64,
    pub t60: f64,
    pub ceiling_db: f64,
    pub floor_db: f64,
    pub crossover_hz: f64,
    pub coherence_floor: f64,
    pub coherence_power: f64,
    pub pan_gamma: f64,
    pub absorption: f64,
    pub early_order: i32,
    pub early_taps: usize,
    pub room_size_m: [f64; 3],
    pub listener_m: [f64; 3],
    pub source_m: [f64; 3],
    pub hrtf_ref_m: f64,
    pub near_lf_hz: f64,
    pub near_lf_max_db: f64,
    pub near_ild_scale: f64,
    pub direct_bleed: f64,
    pub hf_pan_floor: f64,
    pub hf_pan_start_hz: f64,
    pub hf_pan_end_hz: f64,
    pub hf_direct_start_hz: f64,
    pub hf_direct_end_hz: f64,
    pub delays_ms: Vec<f64>,
    pub eq_bands_hz: Vec<f64>,
    pub eq_gain_db: Vec<f64>,
}

impl RouteVTable {
    pub fn load(path: &Path) -> Result<Self, DspError> {
        let text = fs::read_to_string(path).map_err(|source| DspError::Io {
            path: path.to_path_buf(),
            source,
        })?;
        let value: toml::Value = toml::from_str(&text).map_err(|err| DspError::Preset {
            path: path.to_path_buf(),
            reason: err.to_string(),
        })?;
        let table = value.as_table().ok_or_else(|| DspError::Preset {
            path: path.to_path_buf(),
            reason: "preset file is not a table".into(),
        })?;
        let version = table
            .get("version")
            .and_then(toml::Value::as_integer)
            .unwrap_or(1);
        let delays_ms = table
            .get("engine")
            .and_then(|v| v.get("decorrelator_delays_ms"))
            .and_then(f64_vec)
            .unwrap_or_default();
        let layout = table
            .get("layout")
            .and_then(toml::Value::as_table)
            .cloned()
            .unwrap_or_default();
        let mut rows = HashMap::new();
        for (key, item) in table {
            if key == "version" || key == "engine" || key == "layout" {
                continue;
            }
            let Some(preset) = item.as_table() else {
                continue;
            };
            if preset.get("name").and_then(toml::Value::as_str).is_none() {
                continue;
            }
            let mut merged = layout.clone();
            for (k, v) in preset {
                merged.insert(k.clone(), v.clone());
            }
            rows.insert(key.clone(), parse_row(path, key, &merged)?);
        }
        if rows.is_empty() {
            return Err(DspError::Preset {
                path: path.to_path_buf(),
                reason: "no route V presets found".into(),
            });
        }
        Ok(Self {
            version,
            delays_ms,
            rows,
        })
    }

    pub fn get(&self, id: &str) -> Result<&RouteVRow, DspError> {
        self.rows.get(id).ok_or_else(|| DspError::Preset {
            path: Path::new(id).to_path_buf(),
            reason: format!("unknown preset '{id}'"),
        })
    }
}

pub fn map_route_v(
    strength: f64,
    row: &RouteVRow,
    hrtf_name: &str,
    delays_ms: &[f64],
) -> MappedRouteV {
    let s = strength.clamp(0.0, 1.0);
    let front_1 = row.front_az.clamp(30.0, 60.0);
    let (eq_bands_hz, eq_gain_db) = if s <= 0.0 {
        (Vec::new(), Vec::new())
    } else {
        eq_for(row, hrtf_name, s)
    };
    let front = if s <= 0.0 {
        30.0
    } else {
        30.0 + s * (front_1 - 30.0)
    };
    let spread = if row.center_spread_deg > 0.0 {
        if s > 0.0 {
            row.center_spread_deg.min(front)
        } else {
            row.center_spread_deg
        }
    } else {
        let t = ((row.center_distance_m - 0.7) / (row.center_far_m - 0.7).max(0.1)).clamp(0.0, 1.0);
        front * (0.65 + 0.35 * t)
    };
    let bed = if s <= 0.0 { 0.0 } else { s };
    let height_az = if row.height_az.is_empty() {
        vec![45.0, -45.0, 135.0, -135.0]
    } else {
        row.height_az.clone()
    };
    MappedRouteV {
        strength: s,
        bypass: s <= 0.0,
        front_az: front,
        wide_az: row.wide_az,
        surround_az: row.surround_az,
        wide_lin: bed * db(row.wide_db),
        surround_lin: bed * db(row.surround_db),
        height_lin: bed * db(row.height_db),
        early_lin: bed * db(row.early_db),
        late_lin: bed * db(row.room_db),
        height_el: row.height_el,
        height_az,
        height_direct_lin: db(row.height_direct_db),
        center_height_lin: db(row.center_height_db),
        center_distance_m: row.center_distance_m,
        center_spread_deg: spread,
        direct_room_send_lin: db(row.direct_room_send_db),
        surround_delay_ms: row.surround_delay_ms,
        height_rear_delay_ms: row.height_rear_delay_ms,
        direct_height_delay_ms: row.direct_height_delay_ms,
        t60: row.t60,
        ceiling_db: row.ceiling_db,
        floor_db: row.floor_db,
        crossover_hz: row.crossover_hz,
        coherence_floor: row.coherence_floor,
        coherence_power: row.coherence_power,
        pan_gamma: row.pan_gamma,
        absorption: row.absorption,
        early_order: row.early_order,
        early_taps: row.early_taps,
        room_size_m: row.room_size_m,
        listener_m: row.listener_m,
        source_m: row.source_m,
        hrtf_ref_m: row.hrtf_ref_m,
        near_lf_hz: row.near_lf_hz,
        near_lf_max_db: row.near_lf_max_db,
        near_ild_scale: row.near_ild_scale,
        direct_bleed: row.direct_bleed,
        hf_pan_floor: row.hf_pan_floor,
        hf_pan_start_hz: row.hf_pan_start_hz,
        hf_pan_end_hz: row.hf_pan_end_hz,
        hf_direct_start_hz: row.hf_direct_start_hz,
        hf_direct_end_hz: row.hf_direct_end_hz,
        delays_ms: delays_ms.to_vec(),
        eq_bands_hz,
        eq_gain_db,
    }
}

pub fn hrtf_alias(name: &str) -> &str {
    match name {
        "ku100" => "thk_ku100",
        "sadie" | "sadie_d1" => "sadie2_d1",
        other => other,
    }
}

fn db(x: f64) -> f64 {
    10.0_f64.powf(x / 20.0)
}

fn eq_for(row: &RouteVRow, hrtf_name: &str, strength: f64) -> (Vec<f64>, Vec<f64>) {
    let Some(eq) = &row.fixed_eq else {
        return (Vec::new(), Vec::new());
    };
    if eq.bands_hz.is_empty() || eq.strengths.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let Some(matrix) = eq.gain_db.get(hrtf_alias(hrtf_name)) else {
        return (Vec::new(), Vec::new());
    };
    if matrix.len() != eq.strengths.len() {
        return (Vec::new(), Vec::new());
    }
    (
        eq.bands_hz.clone(),
        interp_gains(&eq.strengths, matrix, strength),
    )
}

fn interp_gains(strengths: &[f64], gains: &[Vec<f64>], strength: f64) -> Vec<f64> {
    if strengths.is_empty() || gains.is_empty() {
        return Vec::new();
    }
    if strength <= strengths[0] {
        return gains[0].clone();
    }
    if strength >= strengths[strengths.len() - 1] {
        return gains[gains.len() - 1].clone();
    }
    for i in 0..strengths.len() - 1 {
        let (s0, s1) = (strengths[i], strengths[i + 1]);
        if (s0..=s1).contains(&strength) {
            let span = s1 - s0;
            let t = if span <= 0.0 {
                0.0
            } else {
                (strength - s0) / span
            };
            return gains[i]
                .iter()
                .zip(gains[i + 1].iter())
                .map(|(a, b)| (1.0 - t) * a + t * b)
                .collect();
        }
    }
    gains[gains.len() - 1].clone()
}

fn parse_row(
    path: &Path,
    key: &str,
    row: &toml::map::Map<String, toml::Value>,
) -> Result<RouteVRow, DspError> {
    let num = |name: &str, default: f64| -> f64 {
        row.get(name).and_then(toml_number).unwrap_or(default)
    };
    let vec3 = |name: &str, default: [f64; 3]| -> Result<[f64; 3], DspError> {
        let Some(value) = row.get(name) else {
            return Ok(default);
        };
        let Some(vals) = f64_vec(value) else {
            return Err(DspError::Preset {
                path: path.to_path_buf(),
                reason: format!("preset '{key}' field {name} is not a number list"),
            });
        };
        if vals.len() != 3 {
            return Err(DspError::Preset {
                path: path.to_path_buf(),
                reason: format!("preset '{key}' field {name} must have 3 numbers"),
            });
        }
        Ok([vals[0], vals[1], vals[2]])
    };
    Ok(RouteVRow {
        name: row
            .get("name")
            .and_then(toml::Value::as_str)
            .unwrap_or(key)
            .to_string(),
        front_az: num("front_az", 40.0),
        wide_az: num("wide_az", 90.0),
        surround_az: num("surround_az", 130.0),
        wide_db: num("wide_db", -3.0),
        surround_db: num("surround_db", -8.0),
        surround_delay_ms: num("surround_delay_ms", 18.0),
        height_el: num("height_el", 45.0),
        height_az: row
            .get("height_az")
            .and_then(f64_vec)
            .unwrap_or_else(|| vec![45.0, -45.0, 135.0, -135.0]),
        height_db: num("height_db", -6.0),
        height_direct_db: num("height_direct_db", -14.0),
        center_height_db: num("center_height_db", -22.0),
        height_rear_delay_ms: num("height_rear_delay_ms", 12.0),
        direct_height_delay_ms: num("direct_height_delay_ms", 9.0),
        center_distance_m: num("center_distance_m", 0.9),
        center_spread_deg: num("center_spread_deg", 0.0),
        center_far_m: num("center_far_m", 2.0),
        direct_room_send_db: num("direct_room_send_db", -8.0),
        early_db: num("early_db", -3.0),
        room_db: num("room_db", -6.0),
        t60: num("t60", 0.95),
        ceiling_db: num("ceiling_db", 4.0),
        floor_db: num("floor_db", 0.0),
        crossover_hz: num("crossover_hz", 150.0),
        coherence_floor: num("coherence_floor", 0.22),
        coherence_power: num("coherence_power", 2.0),
        pan_gamma: num("pan_gamma", 1.0),
        absorption: num("absorption", 0.22),
        early_order: num("early_order", 1.0) as i32,
        early_taps: num("early_taps", 12.0).max(0.0) as usize,
        room_size_m: vec3("room_size_m", [7.0, 4.6, 2.7])?,
        listener_m: vec3("listener_m", [2.2, 2.3, 1.25])?,
        source_m: vec3("source_m", [4.8, 2.3, 1.25])?,
        hrtf_ref_m: num("hrtf_ref_m", 1.5),
        near_lf_hz: num("near_lf_hz", 280.0),
        near_lf_max_db: num("near_lf_max_db", 4.0),
        near_ild_scale: num("near_ild_scale", 1.0),
        direct_bleed: num("direct_bleed", 0.0),
        hf_pan_floor: num("hf_pan_floor", 1.0),
        hf_pan_start_hz: num("hf_pan_start_hz", 3000.0),
        hf_pan_end_hz: num("hf_pan_end_hz", 8000.0),
        hf_direct_start_hz: num("hf_direct_start_hz", 0.0),
        hf_direct_end_hz: num("hf_direct_end_hz", 0.0),
        fixed_eq: parse_fixed_eq(row.get("fixed_eq")),
    })
}

fn f64_vec(value: &toml::Value) -> Option<Vec<f64>> {
    let array = value.as_array()?;
    let mut out = Vec::with_capacity(array.len());
    for item in array {
        out.push(toml_number(item)?);
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table() -> RouteVTable {
        RouteVTable::load(&Path::new(env!("CARGO_MANIFEST_DIR")).join("../../presets/route_v.toml"))
            .unwrap()
    }

    #[test]
    fn v2_strength_matches_prototype() {
        let table = table();
        assert_eq!(table.version, 1);
        assert_eq!(table.delays_ms.len(), 12);
        let row = table.get("V2").unwrap();
        let m = map_route_v(1.0, row, "sadie", &table.delays_ms);
        assert!(!m.bypass);
        assert!((m.front_az - 45.0).abs() < 1e-12);
        assert!((m.center_spread_deg - 28.0).abs() < 1e-12);
        assert!((m.wide_lin - 1.122_018_454_301_963_3).abs() < 1e-12);
        assert!((m.surround_lin - 0.630_957_344_480_193_2).abs() < 1e-12);
        assert!((m.height_lin - 0.501_187_233_627_272_2).abs() < 1e-12);
        assert!((m.early_lin - 2.238_721_138_568_339_4).abs() < 1e-12);
        assert!((m.late_lin - 1.584_893_192_461_113_6).abs() < 1e-12);
        assert!((m.center_distance_m - 0.9).abs() < 1e-12);
        assert!((m.direct_room_send_lin - 10.0_f64.powf(-8.0 / 20.0)).abs() < 1e-12);
        assert!((m.eq_gain_db[0] - 2.9).abs() < 1e-12);
        assert_eq!(m.eq_bands_hz.len(), 26);
        assert!((m.t60 - 0.95).abs() < 1e-12);
        assert!((m.hf_pan_floor - 1.0).abs() < 1e-12);
        let half = map_route_v(0.5, row, "sadie", &table.delays_ms);
        assert!((half.front_az - 37.5).abs() < 1e-12);
        assert!((half.center_spread_deg - 28.0).abs() < 1e-12);
        assert!((half.wide_lin - 0.561_009_227_150_981_7).abs() < 1e-12);
        assert!((half.eq_gain_db[0] - (-0.25)).abs() < 1e-12);
        let off = map_route_v(0.0, row, "sadie", &table.delays_ms);
        assert!(off.bypass);
        assert!(off.eq_gain_db.is_empty());
    }

    #[test]
    fn v1_and_v3_override_layout() {
        let table = table();
        let v1 = map_route_v(1.0, table.get("V1").unwrap(), "sadie", &table.delays_ms);
        assert!((v1.center_distance_m - 2.2).abs() < 1e-12);
        assert!((v1.center_spread_deg - 45.0).abs() < 1e-12);
        assert!((v1.direct_room_send_lin - 1.0).abs() < 1e-12);
        assert!((v1.eq_gain_db[0] - 3.34).abs() < 1e-12);
        assert!((v1.wide_az - 90.0).abs() < 1e-12);
        let v3 = map_route_v(1.0, table.get("V3").unwrap(), "sadie", &table.delays_ms);
        assert!((v3.height_el - 55.0).abs() < 1e-12);
        assert!((v3.height_lin - 0.794_328_234_724_281_5).abs() < 1e-12);
        assert!((v3.ceiling_db - 6.0).abs() < 1e-12);
        assert!((v3.height_direct_lin - 10.0_f64.powf(-10.0 / 20.0)).abs() < 1e-12);
        let v1_half = map_route_v(0.5, table.get("V1").unwrap(), "sadie", &table.delays_ms);
        assert!((v1_half.front_az - 37.5).abs() < 1e-12);
        assert!((v1_half.center_spread_deg - 37.5).abs() < 1e-12);
    }
}
