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
    /// Control values that reproduce this preset's stored parameters.
    pub controls: MacroControls,
    /// Input attenuation in dB at strength 1 with this preset's stored
    /// anchors. The engine scales it with the live bed-gain sum.
    pub headroom_db: f64,
    /// Low-shelf boost on the mono path below the crossover, at strength 1.
    pub lf_shelf_hz: f64,
    pub lf_shelf_db: f64,
    /// Peaking trim on that same mono path, at strength 1. 0 dB disables it.
    pub lf_peak_hz: f64,
    pub lf_peak_db: f64,
    pub lf_peak_q: f64,
}

/// User-facing controls, each in `[0, 1]`.
#[derive(Debug, Clone, Copy)]
pub struct MacroControls {
    pub strength: f64,
    pub width: f64,
    pub height: f64,
    pub room: f64,
    pub voice_distance: f64,
}

impl MacroControls {
    pub fn neutral() -> Self {
        Self {
            strength: 1.0,
            width: 0.5,
            height: 0.5,
            room: 0.5,
            voice_distance: 0.5,
        }
    }
}

/// `value = clamp(anchor + (control - default) * per_unit, min, max)`.
/// A zero `per_unit` leaves the anchor untouched.
#[derive(Debug, Clone, Copy)]
pub struct AnchorCurve {
    pub per_unit: f64,
    pub min: f64,
    pub max: f64,
}

impl AnchorCurve {
    fn identity() -> Self {
        Self {
            per_unit: 0.0,
            min: f64::NEG_INFINITY,
            max: f64::INFINITY,
        }
    }

    pub fn eval(self, anchor: f64, control: f64, default: f64) -> f64 {
        let value = anchor + (control - default) * self.per_unit;
        value.clamp(self.min, self.max)
    }
}

/// Linear curves for the four spatial controls. Read from `[macros]`.
#[derive(Debug, Clone, Copy)]
pub struct MacroCurves {
    pub width_front_az: AnchorCurve,
    pub width_wide_db: AnchorCurve,
    pub width_surround_db: AnchorCurve,
    pub height_db: AnchorCurve,
    pub height_el: AnchorCurve,
    pub ceiling_db: AnchorCurve,
    pub height_direct_db: AnchorCurve,
    pub room_db: AnchorCurve,
    pub t60: AnchorCurve,
    pub voice_distance_m: AnchorCurve,
    pub voice_send_db: AnchorCurve,
}

impl MacroCurves {
    fn identity() -> Self {
        Self {
            width_front_az: AnchorCurve::identity(),
            width_wide_db: AnchorCurve::identity(),
            width_surround_db: AnchorCurve::identity(),
            height_db: AnchorCurve::identity(),
            height_el: AnchorCurve::identity(),
            ceiling_db: AnchorCurve::identity(),
            height_direct_db: AnchorCurve::identity(),
            room_db: AnchorCurve::identity(),
            t60: AnchorCurve::identity(),
            voice_distance_m: AnchorCurve::identity(),
            voice_send_db: AnchorCurve::identity(),
        }
    }
}

/// Existing strength law: open `front_az` from `front_az_at_0`, and multiply
/// the named anchor gains. `strength == 0` is still a bypass.
#[derive(Debug, Clone)]
pub struct StrengthScale {
    pub front_az_at_0: f64,
    pub front_az_max: f64,
    pub scales: Vec<String>,
}

impl StrengthScale {
    pub fn legacy() -> Self {
        Self {
            front_az_at_0: 30.0,
            front_az_max: 60.0,
            scales: vec![
                "wide_db".into(),
                "surround_db".into(),
                "height_db".into(),
                "early_db".into(),
                "room_db".into(),
            ],
        }
    }

    fn scales(&self, name: &str) -> bool {
        self.scales.iter().any(|item| item == name)
    }
}

#[derive(Debug, Clone)]
pub struct RouteVTable {
    pub version: i64,
    pub delays_ms: Vec<f64>,
    pub curves: MacroCurves,
    pub strength: StrengthScale,
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
    pub headroom_db: f64,
    pub lf_shelf_hz: f64,
    pub lf_shelf_db: f64,
    pub lf_peak_hz: f64,
    pub lf_peak_db: f64,
    pub lf_peak_q: f64,
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
        let macros = table.get("macros").and_then(toml::Value::as_table);
        let curves = parse_curves(macros);
        let strength = parse_strength(macros);
        let mut rows = HashMap::new();
        for (key, item) in table {
            if key == "version" || key == "engine" || key == "layout" || key == "macros" {
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
            curves,
            strength,
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
    scale: &StrengthScale,
) -> MappedRouteV {
    let s = strength.clamp(0.0, 1.0);
    let open = scale.front_az_at_0;
    let front_1 = row.front_az.clamp(open, scale.front_az_max);
    let (eq_bands_hz, eq_gain_db) = if s <= 0.0 {
        (Vec::new(), Vec::new())
    } else {
        eq_for(row, hrtf_name, s)
    };
    let front = if s <= 0.0 {
        open
    } else {
        open + s * (front_1 - open)
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
    let lin = |name: &str, db_value: f64| -> f64 {
        let gain = db(db_value);
        if scale.scales(name) {
            bed * gain
        } else {
            gain
        }
    };
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
        wide_lin: lin("wide_db", row.wide_db),
        surround_lin: lin("surround_db", row.surround_db),
        height_lin: lin("height_db", row.height_db),
        early_lin: lin("early_db", row.early_db),
        late_lin: lin("room_db", row.room_db),
        height_el: row.height_el,
        height_az,
        height_direct_lin: lin("height_direct_db", row.height_direct_db),
        center_height_lin: lin("center_height_db", row.center_height_db),
        center_distance_m: row.center_distance_m,
        center_spread_deg: spread,
        direct_room_send_lin: lin("direct_room_send_db", row.direct_room_send_db),
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
        headroom_db: row.headroom_db.max(0.0),
        lf_shelf_hz: row.lf_shelf_hz.max(10.0),
        lf_shelf_db: row.lf_shelf_db,
        lf_peak_hz: row.lf_peak_hz.max(10.0),
        lf_peak_db: row.lf_peak_db,
        lf_peak_q: row.lf_peak_q.max(0.1),
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
        controls: parse_controls(row),
        headroom_db: num("headroom_db", 0.0).max(0.0),
        lf_shelf_hz: num("lf_shelf_hz", 70.0).max(10.0),
        lf_shelf_db: num("lf_shelf_db", 0.0),
        lf_peak_hz: num("lf_peak_hz", 125.0).max(10.0),
        lf_peak_db: num("lf_peak_db", 0.0),
        lf_peak_q: num("lf_peak_q", 0.7).max(0.1),
    })
}

fn parse_controls(row: &toml::map::Map<String, toml::Value>) -> MacroControls {
    let Some(table) = row.get("controls").and_then(toml::Value::as_table) else {
        return MacroControls::neutral();
    };
    let num = |name: &str, default: f64| -> f64 {
        table
            .get(name)
            .and_then(toml_number)
            .unwrap_or(default)
            .clamp(0.0, 1.0)
    };
    MacroControls {
        strength: num("strength", 1.0),
        width: num("width", 0.5),
        height: num("height", 0.5),
        room: num("room", 0.5),
        voice_distance: num("voice_distance", 0.5),
    }
}

fn parse_curves(macros: Option<&toml::map::Map<String, toml::Value>>) -> MacroCurves {
    let Some(macros) = macros else {
        return MacroCurves::identity();
    };
    let curve = |group: &str, field: &str| -> AnchorCurve {
        let Some(group_table) = macros.get(group).and_then(toml::Value::as_table) else {
            return AnchorCurve::identity();
        };
        let Some(field_table) = group_table.get(field).and_then(toml::Value::as_table) else {
            return AnchorCurve::identity();
        };
        AnchorCurve {
            per_unit: field_table
                .get("per_unit")
                .and_then(toml_number)
                .unwrap_or(0.0),
            min: field_table
                .get("min")
                .and_then(toml_number)
                .unwrap_or(f64::NEG_INFINITY),
            max: field_table
                .get("max")
                .and_then(toml_number)
                .unwrap_or(f64::INFINITY),
        }
    };
    MacroCurves {
        width_front_az: curve("width", "front_az"),
        width_wide_db: curve("width", "wide_db"),
        width_surround_db: curve("width", "surround_db"),
        height_db: curve("height", "height_db"),
        height_el: curve("height", "height_el"),
        ceiling_db: curve("height", "ceiling_db"),
        height_direct_db: curve("height", "height_direct_db"),
        room_db: curve("room", "room_db"),
        t60: curve("room", "t60"),
        voice_distance_m: curve("voice_distance", "center_distance_m"),
        voice_send_db: curve("voice_distance", "direct_room_send_db"),
    }
}

fn parse_strength(macros: Option<&toml::map::Map<String, toml::Value>>) -> StrengthScale {
    let Some(table) = macros
        .and_then(|m| m.get("strength"))
        .and_then(toml::Value::as_table)
    else {
        return StrengthScale::legacy();
    };
    let scales = table
        .get("scales")
        .and_then(|v| v.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect::<Vec<_>>()
        })
        .filter(|items| !items.is_empty())
        .unwrap_or_else(|| StrengthScale::legacy().scales);
    StrengthScale {
        front_az_at_0: table
            .get("front_az_at_0")
            .and_then(toml_number)
            .unwrap_or(30.0),
        front_az_max: table
            .get("front_az_max")
            .and_then(toml_number)
            .unwrap_or(60.0),
        scales,
    }
}

/// Edit the stored anchors with the four spatial controls. `strength` is not
/// applied here; [`map_route_v`] still does that on the returned row.
pub fn apply_macros(row: &RouteVRow, controls: &MacroControls, curves: &MacroCurves) -> RouteVRow {
    let mut out = row.clone();
    let defaults = row.controls;
    out.front_az = curves
        .width_front_az
        .eval(row.front_az, controls.width, defaults.width);
    out.wide_db = curves
        .width_wide_db
        .eval(row.wide_db, controls.width, defaults.width);
    out.surround_db =
        curves
            .width_surround_db
            .eval(row.surround_db, controls.width, defaults.width);
    out.height_db = curves
        .height_db
        .eval(row.height_db, controls.height, defaults.height);
    out.height_el = curves
        .height_el
        .eval(row.height_el, controls.height, defaults.height);
    out.ceiling_db = curves
        .ceiling_db
        .eval(row.ceiling_db, controls.height, defaults.height);
    out.height_direct_db =
        curves
            .height_direct_db
            .eval(row.height_direct_db, controls.height, defaults.height);
    out.room_db = curves
        .room_db
        .eval(row.room_db, controls.room, defaults.room);
    out.t60 = curves.t60.eval(row.t60, controls.room, defaults.room);
    out.center_distance_m = curves.voice_distance_m.eval(
        row.center_distance_m,
        controls.voice_distance,
        defaults.voice_distance,
    );
    out.direct_room_send_db = curves.voice_send_db.eval(
        row.direct_room_send_db,
        controls.voice_distance,
        defaults.voice_distance,
    );
    out
}

/// Missing controls stay on the preset default. Strength is the explicit value.
pub fn resolve_controls(
    defaults: MacroControls,
    strength: f64,
    width: Option<f64>,
    height: Option<f64>,
    room: Option<f64>,
    voice_distance: Option<f64>,
) -> MacroControls {
    let unit = |value: Option<f64>, default: f64| value.unwrap_or(default).clamp(0.0, 1.0);
    MacroControls {
        strength: strength.clamp(0.0, 1.0),
        width: unit(width, defaults.width),
        height: unit(height, defaults.height),
        room: unit(room, defaults.room),
        voice_distance: unit(voice_distance, defaults.voice_distance),
    }
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
        let m = map_route_v(1.0, row, "sadie", &table.delays_ms, &table.strength);
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
        let half = map_route_v(0.5, row, "sadie", &table.delays_ms, &table.strength);
        assert!((half.front_az - 37.5).abs() < 1e-12);
        assert!((half.center_spread_deg - 28.0).abs() < 1e-12);
        assert!((half.wide_lin - 0.561_009_227_150_981_7).abs() < 1e-12);
        assert!((half.eq_gain_db[0] - (-0.25)).abs() < 1e-12);
        let off = map_route_v(0.0, row, "sadie", &table.delays_ms, &table.strength);
        assert!(off.bypass);
        assert!(off.eq_gain_db.is_empty());
    }

    #[test]
    fn v1_and_v3_override_layout() {
        let table = table();
        let v1 = map_route_v(
            1.0,
            table.get("V1").unwrap(),
            "sadie",
            &table.delays_ms,
            &table.strength,
        );
        assert!((v1.center_distance_m - 2.2).abs() < 1e-12);
        assert!((v1.center_spread_deg - 45.0).abs() < 1e-12);
        assert!((v1.direct_room_send_lin - 1.0).abs() < 1e-12);
        assert!((v1.eq_gain_db[0] - 3.34).abs() < 1e-12);
        assert!((v1.wide_az - 90.0).abs() < 1e-12);
        let v3 = map_route_v(
            1.0,
            table.get("V3").unwrap(),
            "sadie",
            &table.delays_ms,
            &table.strength,
        );
        assert!((v3.height_el - 55.0).abs() < 1e-12);
        assert!((v3.height_lin - 0.794_328_234_724_281_5).abs() < 1e-12);
        assert!((v3.ceiling_db - 6.0).abs() < 1e-12);
        assert!((v3.height_direct_lin - 10.0_f64.powf(-10.0 / 20.0)).abs() < 1e-12);
        let v1_half = map_route_v(
            0.5,
            table.get("V1").unwrap(),
            "sadie",
            &table.delays_ms,
            &table.strength,
        );
        assert!((v1_half.front_az - 37.5).abs() < 1e-12);
        assert!((v1_half.center_spread_deg - 37.5).abs() < 1e-12);
    }

    fn same_bits(a: f64, b: f64) -> bool {
        a.to_bits() == b.to_bits()
    }

    #[test]
    fn default_controls_match_the_stored_preset() {
        let table = table();
        let legacy = StrengthScale::legacy();
        assert!((table.strength.front_az_at_0 - legacy.front_az_at_0).abs() < 1e-12);
        assert_eq!(table.strength.scales, legacy.scales);
        for id in ["V1", "V2", "V3"] {
            let row = table.get(id).unwrap();
            for strength in [0.0_f64, 0.5, 1.0] {
                let mut controls = row.controls;
                controls.strength = strength;
                let anchors = apply_macros(row, &controls, &table.curves);
                assert!(same_bits(anchors.front_az, row.front_az), "{id} front");
                assert!(same_bits(anchors.wide_db, row.wide_db), "{id} wide");
                assert!(
                    same_bits(anchors.surround_db, row.surround_db),
                    "{id} surround"
                );
                assert!(
                    same_bits(anchors.height_db, row.height_db),
                    "{id} height_db"
                );
                assert!(
                    same_bits(anchors.height_el, row.height_el),
                    "{id} height_el"
                );
                assert!(
                    same_bits(anchors.ceiling_db, row.ceiling_db),
                    "{id} ceiling"
                );
                assert!(
                    same_bits(anchors.height_direct_db, row.height_direct_db),
                    "{id} height_direct"
                );
                assert!(same_bits(anchors.room_db, row.room_db), "{id} room");
                assert!(same_bits(anchors.t60, row.t60), "{id} t60");
                assert!(
                    same_bits(anchors.center_distance_m, row.center_distance_m),
                    "{id} distance"
                );
                assert!(
                    same_bits(anchors.direct_room_send_db, row.direct_room_send_db),
                    "{id} send"
                );
                let before = map_route_v(strength, row, "sadie", &table.delays_ms, &legacy);
                let after = map_route_v(
                    strength,
                    &anchors,
                    "sadie",
                    &table.delays_ms,
                    &table.strength,
                );
                assert!(
                    same_bits(before.front_az, after.front_az),
                    "{id} s={strength} front"
                );
                assert!(
                    same_bits(before.wide_lin, after.wide_lin),
                    "{id} s={strength} wide"
                );
                assert!(same_bits(before.surround_lin, after.surround_lin));
                assert!(same_bits(before.height_lin, after.height_lin));
                assert!(same_bits(before.early_lin, after.early_lin));
                assert!(same_bits(before.late_lin, after.late_lin));
                assert!(same_bits(before.height_el, after.height_el));
                assert!(same_bits(before.ceiling_db, after.ceiling_db));
                assert!(same_bits(before.height_direct_lin, after.height_direct_lin));
                assert!(same_bits(before.center_distance_m, after.center_distance_m));
                assert!(same_bits(
                    before.direct_room_send_lin,
                    after.direct_room_send_lin
                ));
                assert!(same_bits(before.t60, after.t60));
                assert!(same_bits(before.center_spread_deg, after.center_spread_deg));
                assert_eq!(before.bypass, after.bypass);
                assert_eq!(before.eq_gain_db, after.eq_gain_db);
            }
        }
    }

    #[test]
    fn controls_move_in_the_documented_direction() {
        let table = table();
        let row = table.get("V3").unwrap();
        let at = |width, height, room, voice| {
            let controls = MacroControls {
                strength: 1.0,
                width,
                height,
                room,
                voice_distance: voice,
            };
            apply_macros(row, &controls, &table.curves)
        };
        let narrow = at(
            0.0,
            row.controls.height,
            row.controls.room,
            row.controls.voice_distance,
        );
        let wide = at(
            1.0,
            row.controls.height,
            row.controls.room,
            row.controls.voice_distance,
        );
        assert!(wide.front_az > narrow.front_az);
        assert!(wide.wide_db > narrow.wide_db);
        assert!(wide.surround_db > narrow.surround_db);
        let low = at(
            row.controls.width,
            0.0,
            row.controls.room,
            row.controls.voice_distance,
        );
        let high = at(
            row.controls.width,
            1.0,
            row.controls.room,
            row.controls.voice_distance,
        );
        assert!(high.height_el > low.height_el);
        assert!(high.height_db > low.height_db);
        assert!(high.ceiling_db > low.ceiling_db);
        let dry = at(
            row.controls.width,
            row.controls.height,
            0.0,
            row.controls.voice_distance,
        );
        let wet = at(
            row.controls.width,
            row.controls.height,
            1.0,
            row.controls.voice_distance,
        );
        assert!(wet.room_db > dry.room_db);
        assert!(wet.t60 > dry.t60);
        let near = at(
            row.controls.width,
            row.controls.height,
            row.controls.room,
            0.0,
        );
        let far = at(
            row.controls.width,
            row.controls.height,
            row.controls.room,
            1.0,
        );
        assert!(far.center_distance_m > near.center_distance_m);
        assert!(far.direct_room_send_db > near.direct_room_send_db);
    }
}
