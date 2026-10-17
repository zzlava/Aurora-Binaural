//! Route V block processor.
//!
//! Virtual front / wide / surround / four height speakers, shoebox early
//! reflections (ceiling and floor kept), a seeded FDN, a proximity shelf on
//! the voice pair, and the preset's fixed minimum-phase FIR. Every gain,
//! angle, delay and EQ coefficient comes from the route V preset table.
//! `strength == 0` returns the stereo input and reports zero latency.
//!
//! HRIR placement uses the existing uniform partitioned convolver. That adds
//! one partition of delay relative to the prototype's `fftconvolve`. The low
//! band and the FDN, which are not HRIR-convolved, are delayed by the same
//! partition so the mix lines up. Below the crossover each ear keeps its own
//! channel. `(L+R)/2` cancels bass when left and right are weakly correlated,
//! which is normal for spaced omnis in a hall. The two ears share one shelf
//! and one peak, with separate filter state, so a mono low band stays
//! coherent. `process` does not allocate.

use crate::conv::{DelayLine, UniformConvolver};
use crate::coords::{az_el_to_cart, cart_to_az_el};
use crate::crossover::{apply_sos, butterworth2_low, Lr4Crossover};
use crate::error::DspError;
use crate::fdn::FdnReverb;
use crate::hrir::HrirPack;
use crate::interpolate::idw_render;
use crate::limiter::{LimiterStats, TruePeakLimiter, DETECTOR_CEILING_DBTP, LOOKAHEAD};
use crate::preset_v::{
    apply_macros, map_route_v, resolve_controls, MacroControls, MappedRouteV, RouteVRow,
    RouteVTable,
};
use crate::processor::{Params, Processor, RenderConfig};
use crate::rng::numpy_round;
use crate::upmix::{StreamingUpmix, N_FFT, N_SECTORS, N_STEMS};
use num_complex::Complex;
use realfft::RealFftPlanner;

const PART: usize = 64;
/// 32 ms at 48 kHz. Gain ramps and HRIR crossfades share this length.
const SMOOTH: usize = 1536;
const FADE: usize = SMOOTH;
const N_GAINS: usize = 8;
const G_WIDE: usize = 0;
const G_SUR: usize = 1;
const G_HEIGHT: usize = 2;
const G_EARLY: usize = 3;
const G_LATE: usize = 4;
const G_HDIRECT: usize = 5;
const G_CHEIGHT: usize = 6;
const G_SEND: usize = 7;
const MAX_EARLY: usize = 16;
const EQ_TAPS: usize = 512;
const EQ_FFT: usize = 8192;
const N_HEIGHT: usize = 4;
const N_LATERAL: usize = N_SECTORS - 1;
const CENTER_POS: usize = 0;
const CENTER_NEG: usize = 1;
const LAT0: usize = 2;
const WIDE_L: usize = LAT0 + N_LATERAL;
const WIDE_R: usize = WIDE_L + 1;
const SUR_L: usize = WIDE_R + 1;
const SUR_R: usize = SUR_L + 1;
const H0: usize = SUR_R + 1;
const EARLY0: usize = H0 + N_HEIGHT;

pub struct RouteV {
    engine: Option<Engine>,
}

struct EarlyTap {
    delay: usize,
    gain: f64,
    azimuth_deg: f64,
    elevation_deg: f64,
}

struct PolarityDelay {
    line: DelayLine,
    sign: f64,
}

impl PolarityDelay {
    fn new(samples: usize, sign: f64) -> Self {
        Self {
            line: DelayLine::new(samples),
            sign,
        }
    }

    fn process(&mut self, input: &[f64], output: &mut [f64]) {
        self.line.process(input, output);
        if self.sign < 0.0 {
            for sample in output.iter_mut() {
                *sample = -*sample;
            }
        }
    }

    fn reset(&mut self) {
        self.line.reset();
    }
}

struct FirStereo {
    coeff: Vec<f64>,
    hist_l: Vec<f64>,
    hist_r: Vec<f64>,
    pos: usize,
}

impl FirStereo {
    fn empty() -> Self {
        Self {
            coeff: Vec::new(),
            hist_l: Vec::new(),
            hist_r: Vec::new(),
            pos: 0,
        }
    }

    fn set_coeff(&mut self, coeff: Vec<f64>) {
        let n = coeff.len();
        self.coeff = coeff;
        self.hist_l = vec![0.0; n.max(1)];
        self.hist_r = vec![0.0; n.max(1)];
        self.pos = 0;
    }

    fn reset(&mut self) {
        self.hist_l.fill(0.0);
        self.hist_r.fill(0.0);
        self.pos = 0;
    }

    fn blend_toward(&mut self, target: &[f64], steps: usize, left: usize) {
        if left == 0 || steps == 0 || self.coeff.is_empty() || self.coeff.len() != target.len() {
            return;
        }
        let portion = steps as f64 / left as f64;
        for i in 0..self.coeff.len() {
            self.coeff[i] += (target[i] - self.coeff[i]) * portion;
        }
    }

    fn copy_coeff_inplace(&mut self, target: &[f64]) {
        if self.coeff.len() == target.len() {
            self.coeff.copy_from_slice(target);
        }
    }

    fn process(&mut self, left: &[f64], right: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = left
            .len()
            .min(right.len())
            .min(out_l.len())
            .min(out_r.len());
        let taps = self.coeff.len();
        if taps == 0 {
            out_l[..n].copy_from_slice(&left[..n]);
            out_r[..n].copy_from_slice(&right[..n]);
            return;
        }
        for i in 0..n {
            self.hist_l[self.pos] = left[i];
            self.hist_r[self.pos] = right[i];
            let mut acc_l = 0.0;
            let mut acc_r = 0.0;
            let mut p = self.pos;
            for k in 0..taps {
                acc_l += self.coeff[k] * self.hist_l[p];
                acc_r += self.coeff[k] * self.hist_r[p];
                p = if p == 0 { taps - 1 } else { p - 1 };
            }
            out_l[i] = acc_l;
            out_r[i] = acc_r;
            self.pos += 1;
            if self.pos == taps {
                self.pos = 0;
            }
        }
    }
}

struct Engine {
    max_block: usize,
    bypass: bool,
    limiter_on: bool,
    mapped: MappedRouteV,
    controls: MacroControls,
    anchors: RouteVRow,
    seed: u64,
    hrtf_name: String,
    table: RouteVTable,
    pack: HrirPack,
    hrir_peak: usize,
    xo: Lr4Crossover,
    up: StreamingUpmix,
    dec_wide_l: PolarityDelay,
    dec_wide_r: PolarityDelay,
    dec_sur_l: PolarityDelay,
    dec_sur_r: PolarityDelay,
    dec_height: [PolarityDelay; N_HEIGHT],
    delay_sur_l: DelayLine,
    delay_sur_r: DelayLine,
    delay_height_l: DelayLine,
    delay_height_r: DelayLine,
    delay_lift: DelayLine,
    delay_early: Vec<DelayLine>,
    delay_low: DelayLine,
    delay_low_r: DelayLine,
    delay_fdn_l: DelayLine,
    delay_fdn_r: DelayLine,
    conv: Vec<UniformConvolver>,
    early: Vec<EarlyTap>,
    fdn: FdnReverb,
    fir: FirStereo,
    limiter: TruePeakLimiter,
    /// Bed-gain sum of this preset at strength 1 with its stored anchors.
    bed_ref: f64,
    headroom_now: f64,
    headroom_d: f64,
    headroom_tgt: f64,
    lf_k_now: f64,
    lf_k_d: f64,
    lf_k_tgt: f64,
    lf_sos: [f64; 6],
    lf_zi: [f64; 2],
    lf_peak_sos: [f64; 6],
    lf_peak_zi: [f64; 2],
    lf_zi_r: [f64; 2],
    lf_peak_zi_r: [f64; 2],
    /// Smoothed low-band correlation detector. The boost follows it slowly
    /// so a phrase cannot pump the 120 Hz peak.
    rho_pl: f64,
    rho_pr: f64,
    rho_c: f64,
    rho_a: f64,
    rho_slew: f64,
    rho_hp_a: f64,
    rho_hp_yl: f64,
    rho_hp_yr: f64,
    rho_hp_xl: f64,
    rho_hp_xr: f64,
    rho_db_now: f64,
    rho_applied: f64,
    rho_sos: [f64; 6],
    rho_zi_l: [f64; 2],
    rho_zi_r: [f64; 2],
    lf_shelf_applied: f64,
    lf_peak_applied: f64,
    center_i: usize,
    lateral_idx: [usize; N_LATERAL],
    in_l: Vec<f64>,
    in_r: Vec<f64>,
    low_l: Vec<f64>,
    low_r: Vec<f64>,
    high_l: Vec<f64>,
    high_r: Vec<f64>,
    stems: Vec<f64>,
    lateral: Vec<f64>,
    mono: Vec<f64>,
    feed: Vec<f64>,
    lifted: Vec<f64>,
    room: Vec<f64>,
    fdn_l: Vec<f64>,
    fdn_r: Vec<f64>,
    tmp_l: Vec<f64>,
    tmp_r: Vec<f64>,
    mix_l: Vec<f64>,
    mix_r: Vec<f64>,
    ir_l: Vec<f64>,
    ir_r: Vec<f64>,
    shelf: Vec<f64>,
    played: bool,
    smooth_left: usize,
    gain_now: [f64; N_GAINS],
    gain_d: [f64; N_GAINS],
    gain_tgt: [f64; N_GAINS],
    early_now: [f64; MAX_EARLY],
    early_d: [f64; MAX_EARLY],
    early_tgt: [f64; MAX_EARLY],
    front_now: f64,
    front_d: f64,
    front_tgt: f64,
    fir_tgt: Vec<f64>,
    xfade_left: usize,
    xfade_len: usize,
}

impl RouteV {
    pub fn new() -> Self {
        Self { engine: None }
    }

    fn latency_of(engine: &Engine) -> usize {
        if engine.bypass {
            return 0;
        }
        let lim = if engine.limiter_on { LOOKAHEAD } else { 0 };
        N_FFT + engine.hrir_peak + PART + lim
    }
}

impl Default for RouteV {
    fn default() -> Self {
        Self::new()
    }
}

/// Controls and the internal parameters they expanded to. Reported values are
/// the requested target, including while a ramp is still in progress.
#[derive(Debug, Clone)]
pub struct EffectiveParams {
    pub strength: f64,
    pub width: f64,
    pub height: f64,
    pub room: f64,
    pub voice_distance: f64,
    pub front_az: f64,
    pub wide_db: f64,
    pub surround_db: f64,
    pub height_db: f64,
    pub height_el: f64,
    pub height_direct_db: f64,
    pub ceiling_db: f64,
    pub room_db: f64,
    pub early_db: f64,
    pub t60: f64,
    pub direct_room_send_db: f64,
    pub center_distance_m: f64,
    pub center_spread_deg: f64,
    pub wide_lin: f64,
    pub surround_lin: f64,
    pub height_lin: f64,
    pub early_lin: f64,
    pub late_lin: f64,
    pub direct_room_send_lin: f64,
    pub bypass: bool,
    /// Target input attenuation in dB (positive means quieter). 0 while bypassed.
    pub headroom_db: f64,
}

impl RouteV {
    pub fn effective_params(&self) -> Option<EffectiveParams> {
        let engine = self.engine.as_ref()?;
        let anchors = &engine.anchors;
        let mapped = &engine.mapped;
        let controls = engine.controls;
        Some(EffectiveParams {
            strength: controls.strength,
            width: controls.width,
            height: controls.height,
            room: controls.room,
            voice_distance: controls.voice_distance,
            front_az: mapped.front_az,
            wide_db: anchors.wide_db,
            surround_db: anchors.surround_db,
            height_db: anchors.height_db,
            height_el: mapped.height_el,
            height_direct_db: anchors.height_direct_db,
            ceiling_db: mapped.ceiling_db,
            room_db: anchors.room_db,
            early_db: anchors.early_db,
            t60: mapped.t60,
            direct_room_send_db: anchors.direct_room_send_db,
            center_distance_m: mapped.center_distance_m,
            center_spread_deg: mapped.center_spread_deg,
            wide_lin: mapped.wide_lin,
            surround_lin: mapped.surround_lin,
            height_lin: mapped.height_lin,
            early_lin: mapped.early_lin,
            late_lin: mapped.late_lin,
            direct_room_send_lin: mapped.direct_room_send_lin,
            bypass: mapped.bypass,
            headroom_db: engine.headroom_db_value(),
        })
    }

    /// Target input attenuation in dB. Zero while strength 0 is bypassing.
    pub fn headroom_db(&self) -> f64 {
        self.engine
            .as_ref()
            .map(Engine::headroom_db_value)
            .unwrap_or(0.0)
    }

    pub fn limiter_stats(&self) -> LimiterStats {
        self.engine
            .as_ref()
            .map(|engine| engine.limiter.stats())
            .unwrap_or(LimiterStats {
                max_gr_db: 0.0,
                p50_gr_db: 0.0,
                p95_gr_db: 0.0,
                samples: 0,
            })
    }
}

impl Processor for RouteV {
    fn prepare(
        &mut self,
        sample_rate: u32,
        max_block: usize,
        config: &RenderConfig,
    ) -> Result<(), DspError> {
        if sample_rate != 48_000 {
            return Err(DspError::SampleRate(sample_rate));
        }
        if max_block == 0 {
            return Err(DspError::BlockSize(max_block));
        }
        if config.input_channels != 1 && config.input_channels != 2 {
            return Err(DspError::InputChannels(config.input_channels));
        }
        if config.output_channels != 2 {
            return Err(DspError::OutputChannels(config.output_channels));
        }
        let table = RouteVTable::load(&config.preset_path)?;
        let row = table.get(&config.preset)?.clone();
        let hrtf_name =
            resolve_hrtf_name(&config.hrtf_name, &config.hrtf_pack, &config.hrtf_source);
        let controls = resolve_controls(row.controls, config.strength, None, None, None, None);
        let anchors = apply_macros(&row, &controls, &table.curves);
        let mapped = map_route_v(
            controls.strength,
            &anchors,
            &hrtf_name,
            &table.delays_ms,
            &table.strength,
        );
        let pack = HrirPack::load(&config.hrtf_pack, &config.hrtf_source)?;
        if pack.sample_rate != sample_rate {
            return Err(DspError::Message(format!(
                "HRIR pack sample rate is {}, refusing to fall back to another rate",
                pack.sample_rate
            )));
        }
        if !pack.eq_mode.contains("frontal") {
            return Err(DspError::Message(format!(
                "HRIR pack eq mode '{}' is not the frontal equalization route V convolves",
                pack.eq_mode
            )));
        }
        let bed_ref = bed_sum(&map_route_v(
            1.0,
            &row,
            &hrtf_name,
            &table.delays_ms,
            &table.strength,
        ))
        .max(1e-9);
        let n_taps = pack.n_taps().max(1);
        let n_early = mapped.early_taps.max(1);
        let n_conv = EARLY0 + n_early;
        let mb = max_block;
        let mut conv = Vec::with_capacity(n_conv);
        for _ in 0..n_conv {
            conv.push(UniformConvolver::new(PART, n_taps, FADE));
        }
        let mut up = StreamingUpmix::new(f64::from(sample_rate), mb, mapped.front_az, 1.0);
        apply_upmix_controls(&mut up, &mapped);
        let mut engine = Engine {
            max_block: mb,
            bypass: mapped.bypass,
            limiter_on: config.limiter,
            mapped,
            controls,
            anchors,
            seed: config.seed,
            hrtf_name,
            table,
            pack,
            hrir_peak: 0,
            xo: Lr4Crossover::new(f64::from(sample_rate), row.crossover_hz),
            up,
            dec_wide_l: PolarityDelay::new(1, 1.0),
            dec_wide_r: PolarityDelay::new(1, 1.0),
            dec_sur_l: PolarityDelay::new(1, 1.0),
            dec_sur_r: PolarityDelay::new(1, 1.0),
            dec_height: [
                PolarityDelay::new(1, 1.0),
                PolarityDelay::new(1, 1.0),
                PolarityDelay::new(1, 1.0),
                PolarityDelay::new(1, 1.0),
            ],
            delay_sur_l: DelayLine::new(1),
            delay_sur_r: DelayLine::new(1),
            delay_height_l: DelayLine::new(1),
            delay_height_r: DelayLine::new(1),
            delay_lift: DelayLine::new(1),
            delay_early: Vec::new(),
            delay_low: DelayLine::new(1),
            delay_low_r: DelayLine::new(1),
            delay_fdn_l: DelayLine::new(PART),
            delay_fdn_r: DelayLine::new(PART),
            conv,
            early: Vec::new(),
            fdn: FdnReverb::new(sample_rate, row.t60, config.seed),
            fir: FirStereo::empty(),
            limiter: TruePeakLimiter::with_ceiling(sample_rate, DETECTOR_CEILING_DBTP),
            bed_ref,
            headroom_now: 1.0,
            headroom_d: 0.0,
            headroom_tgt: 1.0,
            lf_k_now: 0.0,
            lf_k_d: 0.0,
            lf_k_tgt: 0.0,
            lf_sos: [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            lf_zi: [0.0; 2],
            lf_peak_sos: [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            lf_peak_zi: [0.0; 2],
            lf_zi_r: [0.0; 2],
            lf_peak_zi_r: [0.0; 2],
            rho_pl: 0.0,
            rho_pr: 0.0,
            rho_c: 0.0,
            rho_a: 1.0,
            rho_slew: 0.0,
            rho_hp_a: 1.0,
            rho_hp_yl: 0.0,
            rho_hp_yr: 0.0,
            rho_hp_xl: 0.0,
            rho_hp_xr: 0.0,
            rho_db_now: 0.0,
            rho_applied: f64::NAN,
            rho_sos: [1.0, 0.0, 0.0, 1.0, 0.0, 0.0],
            rho_zi_l: [0.0; 2],
            rho_zi_r: [0.0; 2],
            lf_shelf_applied: f64::NAN,
            lf_peak_applied: f64::NAN,
            center_i: N_SECTORS / 2,
            lateral_idx: [0; N_LATERAL],
            in_l: vec![0.0; mb],
            in_r: vec![0.0; mb],
            low_l: vec![0.0; mb],
            low_r: vec![0.0; mb],
            high_l: vec![0.0; mb],
            high_r: vec![0.0; mb],
            stems: vec![0.0; N_STEMS * mb],
            lateral: vec![0.0; mb],
            mono: vec![0.0; mb],
            feed: vec![0.0; mb],
            lifted: vec![0.0; mb],
            room: vec![0.0; mb],
            fdn_l: vec![0.0; mb],
            fdn_r: vec![0.0; mb],
            tmp_l: vec![0.0; mb],
            tmp_r: vec![0.0; mb],
            mix_l: vec![0.0; mb],
            mix_r: vec![0.0; mb],
            ir_l: vec![0.0; n_taps],
            ir_r: vec![0.0; n_taps],
            shelf: vec![0.0; n_taps],
            played: false,
            smooth_left: 0,
            gain_now: [0.0; N_GAINS],
            gain_d: [0.0; N_GAINS],
            gain_tgt: [0.0; N_GAINS],
            early_now: [0.0; MAX_EARLY],
            early_d: [0.0; MAX_EARLY],
            early_tgt: [0.0; MAX_EARLY],
            front_now: 0.0,
            front_d: 0.0,
            front_tgt: 0.0,
            fir_tgt: Vec::new(),
            xfade_left: 0,
            xfade_len: 1,
        };
        engine.install(sample_rate);
        engine.capture_smooth();
        self.engine = Some(engine);
        Ok(())
    }

    fn set_params(&mut self, params: &Params) {
        let Some(engine) = self.engine.as_mut() else {
            return;
        };
        let Ok(row) = engine.table.get(&params.preset).cloned() else {
            return;
        };
        let controls = resolve_controls(
            row.controls,
            params.strength,
            params.width,
            params.height,
            params.room,
            params.voice_distance,
        );
        engine.bed_ref = bed_sum(&map_route_v(
            1.0,
            &row,
            &engine.hrtf_name,
            &engine.table.delays_ms,
            &engine.table.strength,
        ))
        .max(1e-9);
        let anchors = apply_macros(&row, &controls, &engine.table.curves);
        let mapped = map_route_v(
            controls.strength,
            &anchors,
            &engine.hrtf_name,
            &engine.table.delays_ms,
            &engine.table.strength,
        );
        engine.controls = controls;
        engine.anchors = anchors;
        if engine.played {
            engine.begin_smooth(mapped);
            return;
        }
        let cutoff_changed = (mapped.crossover_hz - engine.mapped.crossover_hz).abs() > 1e-9;
        let t60_changed = (mapped.t60 - engine.mapped.t60).abs() > 1e-12;
        let seed_changed = params.seed != engine.seed;
        engine.mapped = mapped;
        engine.bypass = engine.mapped.bypass;
        engine.up.set_width_ambient(engine.mapped.front_az, 1.0);
        apply_upmix_controls(&mut engine.up, &engine.mapped);
        if cutoff_changed {
            engine.xo = Lr4Crossover::new(48_000.0, engine.mapped.crossover_hz);
        }
        if seed_changed || t60_changed {
            engine.seed = params.seed;
            engine.fdn.reseed(48_000, engine.mapped.t60, params.seed);
        }
        engine.install(48_000);
        engine.capture_smooth();
    }

    fn process(&mut self, input: &[&[f32]], output: &mut [&mut [f32]], n: usize) {
        if output.len() < 2 || n == 0 {
            return;
        }
        let n = n.min(output[0].len()).min(output[1].len());
        let Some(engine) = self.engine.as_mut() else {
            for i in 0..n {
                output[0][i] = 0.0;
                output[1][i] = 0.0;
            }
            return;
        };
        let channels = input.len();
        let mut done = 0;
        while done < n {
            let m = (n - done).min(engine.max_block);
            for i in 0..m {
                let l = if channels >= 1 && done + i < input[0].len() {
                    finite(input[0][done + i])
                } else {
                    0.0
                };
                let r = if channels >= 2 && done + i < input[1].len() {
                    finite(input[1][done + i])
                } else {
                    l
                };
                engine.in_l[i] = f64::from(l);
                engine.in_r[i] = f64::from(r);
            }
            engine.process_block(m);
            for i in 0..m {
                output[0][done + i] = finite32(engine.mix_l[i]);
                output[1][done + i] = finite32(engine.mix_r[i]);
            }
            done += m;
        }
    }

    fn latency_samples(&self) -> usize {
        self.engine.as_ref().map(RouteV::latency_of).unwrap_or(0)
    }

    fn tail_samples(&self) -> usize {
        match &self.engine {
            Some(engine) if !engine.bypass => {
                let seconds = (engine.mapped.t60 * 1.15).clamp(0.35, 1.05);
                (seconds * 48_000.0) as usize
            }
            _ => 0,
        }
    }

    fn flush(&mut self, output: &mut [&mut [f32]]) -> usize {
        if output.len() < 2 {
            return 0;
        }
        let want = self.latency_samples() + self.tail_samples();
        let n = want.min(output[0].len()).min(output[1].len());
        if n == 0 {
            return 0;
        }
        let Some(engine) = self.engine.as_mut() else {
            return 0;
        };
        let mut done = 0;
        while done < n {
            let m = (n - done).min(engine.max_block);
            engine.in_l[..m].fill(0.0);
            engine.in_r[..m].fill(0.0);
            if engine.bypass {
                engine.mix_l[..m].fill(0.0);
                engine.mix_r[..m].fill(0.0);
            } else {
                engine.process_block(m);
            }
            for i in 0..m {
                output[0][done + i] = finite32(engine.mix_l[i]);
                output[1][done + i] = finite32(engine.mix_r[i]);
            }
            done += m;
        }
        n
    }

    fn reset(&mut self) {
        let Some(engine) = self.engine.as_mut() else {
            return;
        };
        engine.xo.reset();
        engine.up.reset();
        engine.dec_wide_l.reset();
        engine.dec_wide_r.reset();
        engine.dec_sur_l.reset();
        engine.dec_sur_r.reset();
        for dec in &mut engine.dec_height {
            dec.reset();
        }
        engine.delay_sur_l.reset();
        engine.delay_sur_r.reset();
        engine.delay_height_l.reset();
        engine.delay_height_r.reset();
        engine.delay_lift.reset();
        for delay in &mut engine.delay_early {
            delay.reset();
        }
        engine.delay_low.reset();
        engine.delay_low_r.reset();
        engine.delay_fdn_l.reset();
        engine.delay_fdn_r.reset();
        for conv in &mut engine.conv {
            conv.reset();
        }
        engine.fdn.reset();
        engine.fir.reset();
        engine.limiter.reset();
        engine.lf_zi = [0.0; 2];
        engine.lf_peak_zi = [0.0; 2];
        engine.lf_zi_r = [0.0; 2];
        engine.lf_peak_zi_r = [0.0; 2];
        engine.reset_rho();
        engine.lf_shelf_applied = f64::NAN;
        engine.lf_peak_applied = f64::NAN;
    }
}

impl Engine {
    fn install(&mut self, sample_rate: u32) {
        let mapped = self.mapped.clone();
        self.dec_wide_l = polarity(sample_rate, self.seed + 1, &mapped.delays_ms);
        self.dec_wide_r = polarity(sample_rate, self.seed + 2, &mapped.delays_ms);
        self.dec_sur_l = polarity(sample_rate, self.seed + 3, &mapped.delays_ms);
        self.dec_sur_r = polarity(sample_rate, self.seed + 4, &mapped.delays_ms);
        self.dec_height = [
            polarity(sample_rate, self.seed + 5, &mapped.delays_ms),
            polarity(sample_rate, self.seed + 6, &mapped.delays_ms),
            polarity(sample_rate, self.seed + 7, &mapped.delays_ms),
            polarity(sample_rate, self.seed + 8, &mapped.delays_ms),
        ];
        self.delay_sur_l = DelayLine::new(ms_samples(mapped.surround_delay_ms, sample_rate).max(1));
        self.delay_sur_r = DelayLine::new(ms_samples(mapped.surround_delay_ms, sample_rate).max(1));
        self.delay_height_l =
            DelayLine::new(ms_samples(mapped.height_rear_delay_ms, sample_rate).max(1));
        self.delay_height_r =
            DelayLine::new(ms_samples(mapped.height_rear_delay_ms, sample_rate).max(1));
        self.delay_lift =
            DelayLine::new(ms_samples(mapped.direct_height_delay_ms, sample_rate).max(1));
        self.early = shoebox_taps(&mapped, sample_rate);
        self.delay_early = self
            .early
            .iter()
            .map(|tap| DelayLine::new(tap.delay.max(1)))
            .collect();
        self.hrir_peak = self.peak_at(0.0, 0.0);
        self.up.set_width_ambient(mapped.front_az, 1.0);
        self.center_i = center_index(&self.up.sector_az);
        let mut k = 0;
        self.lateral_idx = [0; N_LATERAL];
        for i in 0..N_SECTORS {
            if i != self.center_i && k < N_LATERAL {
                self.lateral_idx[k] = i;
                k += 1;
            }
        }
        let spread = mapped.center_spread_deg;
        self.set_near_conv(CENTER_POS, spread, 0.0);
        self.set_near_conv(CENTER_NEG, -spread, 0.0);
        let laterals = self.lateral_idx;
        let sector_az = self.up.sector_az;
        for (k, stem) in laterals.into_iter().enumerate() {
            self.set_conv(LAT0 + k, sector_az[stem], 0.0);
        }
        self.set_conv(WIDE_L, mapped.wide_az, 0.0);
        self.set_conv(WIDE_R, -mapped.wide_az, 0.0);
        self.set_conv(SUR_L, mapped.surround_az, 0.0);
        self.set_conv(SUR_R, -mapped.surround_az, 0.0);
        let height_az = pad_height(&mapped.height_az);
        for i in 0..N_HEIGHT {
            self.set_conv(H0 + i, height_az[i], mapped.height_el);
        }
        let n_early = self.early.len().min(self.conv.len().saturating_sub(EARLY0));
        for i in 0..n_early {
            let tap = &self.early[i];
            self.set_conv(EARLY0 + i, tap.azimuth_deg, tap.elevation_deg);
        }
        let low_delay = N_FFT + self.hrir_peak + PART;
        self.delay_low = DelayLine::new(low_delay.max(1));
        self.delay_low_r = DelayLine::new(low_delay.max(1));
        self.lf_shelf_applied = f64::NAN;
        self.lf_peak_applied = f64::NAN;
        self.lf_zi = [0.0; 2];
        self.lf_peak_zi = [0.0; 2];
        self.lf_zi_r = [0.0; 2];
        self.lf_peak_zi_r = [0.0; 2];
        self.arm_rho();
        if mapped.bypass || mapped.eq_gain_db.is_empty() || mapped.eq_bands_hz.is_empty() {
            self.fir.set_coeff(Vec::new());
        } else {
            let coeff = design_minphase(
                &mapped.eq_bands_hz,
                &mapped.eq_gain_db,
                sample_rate,
                EQ_TAPS,
                EQ_FFT,
            );
            self.fir.set_coeff(coeff);
        }
    }

    fn peak_at(&mut self, az: f64, el: f64) -> usize {
        let taps = self.ir_l.len();
        idw_render(&self.pack, az, el, 3, &mut self.ir_l, &mut self.ir_r);
        let mut best = 0usize;
        let mut mag = -1.0;
        for i in 0..taps {
            let m = self.ir_l[i].abs() + self.ir_r[i].abs();
            if m > mag {
                mag = m;
                best = i;
            }
        }
        best
    }

    fn set_conv(&mut self, index: usize, az: f64, el: f64) {
        if index >= self.conv.len() {
            return;
        }
        idw_render(&self.pack, az, el, 3, &mut self.ir_l, &mut self.ir_r);
        // Only the height speakers. Wide and surround carry decorrelated
        // energy too, but the 500–800 Hz hump measured on hall recordings
        // collapsed when the height layer was removed and barely moved
        // when wide or surround was removed. A cut here leaves correlated
        // pop, which stays in the center pair, alone.
        if (H0..H0 + N_HEIGHT).contains(&index) {
            let hz = self.mapped.bed_notch_hz;
            let db = self.mapped.bed_notch_db;
            let q = self.mapped.bed_notch_q;
            color_bed(&mut self.ir_l, hz, db, q);
            color_bed(&mut self.ir_r, hz, db, q);
        }
        self.conv[index].set_ir(&self.ir_l, &self.ir_r);
    }

    fn set_near_conv(&mut self, index: usize, az: f64, el: f64) {
        if index >= self.conv.len() {
            return;
        }
        idw_render(&self.pack, az, el, 3, &mut self.ir_l, &mut self.ir_r);
        let (db_l, db_r) = nearfield_db(
            az,
            el,
            self.mapped.center_distance_m,
            self.mapped.hrtf_ref_m,
            self.mapped.near_ild_scale,
            self.mapped.near_lf_max_db,
        );
        let db_l = round_dp(db_l, 2);
        let db_r = round_dp(db_r, 2);
        if db_l.abs() > 0.05 || db_r.abs() > 0.05 {
            let hz = self.mapped.near_lf_hz;
            let sr = 48_000.0;
            apply_low_shelf(&mut self.ir_l, &mut self.shelf, sr, hz, db_l);
            apply_low_shelf(&mut self.ir_r, &mut self.shelf, sr, hz, db_r);
        }
        self.conv[index].set_ir(&self.ir_l, &self.ir_r);
    }

    fn capture_smooth(&mut self) {
        self.gain_now = gain_pack(&self.mapped);
        self.gain_tgt = self.gain_now;
        self.gain_d = [0.0; N_GAINS];
        self.early_now = [0.0; MAX_EARLY];
        self.early_d = [0.0; MAX_EARLY];
        self.early_tgt = [0.0; MAX_EARLY];
        for (i, tap) in self.early.iter().take(MAX_EARLY).enumerate() {
            self.early_now[i] = tap.gain;
            self.early_tgt[i] = tap.gain;
        }
        self.front_now = self.mapped.front_az;
        self.front_tgt = self.front_now;
        self.front_d = 0.0;
        let (head, shelf_k) = self.level_targets();
        self.headroom_now = head;
        self.headroom_tgt = head;
        self.headroom_d = 0.0;
        self.lf_k_now = shelf_k;
        self.lf_k_tgt = shelf_k;
        self.lf_k_d = 0.0;
        self.smooth_left = 0;
        self.fir_tgt.clear();
        self.xfade_left = 0;
    }

    fn g(&self, idx: usize, sample: usize) -> f64 {
        if self.smooth_left == 0 {
            return self.gain_now[idx];
        }
        self.gain_now[idx] + self.gain_d[idx] * sample.min(self.smooth_left) as f64
    }

    fn g_front(&self, sample: usize) -> f64 {
        if self.smooth_left == 0 {
            return self.front_now;
        }
        self.front_now + self.front_d * sample.min(self.smooth_left) as f64
    }

    fn acc_gain(&mut self, idx: usize, m: usize) {
        let now = self.gain_now[idx];
        let delta = self.gain_d[idx];
        let left = self.smooth_left;
        for i in 0..m {
            let gain = if left == 0 {
                now
            } else {
                now + delta * i.min(left) as f64
            };
            self.mix_l[i] += gain * self.tmp_l[i];
            self.mix_r[i] += gain * self.tmp_r[i];
        }
    }

    fn early_g(&self, tap: usize, sample: usize) -> f64 {
        if tap >= MAX_EARLY {
            return 0.0;
        }
        if self.smooth_left == 0 {
            return self.early_now[tap];
        }
        self.early_now[tap] + self.early_d[tap] * sample.min(self.smooth_left) as f64
    }

    fn begin_smooth(&mut self, mapped: MappedRouteV) {
        let old = self.mapped.clone();
        let leaving_bypass = self.bypass && !mapped.bypass;
        let steps = SMOOTH.max(1) as f64;
        self.gain_tgt = gain_pack(&mapped);
        for i in 0..N_GAINS {
            self.gain_d[i] = (self.gain_tgt[i] - self.gain_now[i]) / steps;
        }
        self.front_tgt = mapped.front_az;
        self.front_d = (mapped.front_az - self.front_now) / steps;
        let shelf_hz = mapped.lf_shelf_hz;
        let peak_hz = mapped.lf_peak_hz;
        let peak_q = mapped.lf_peak_q;
        let rho_hz = mapped.lf_rho_hz;
        let rho_q = mapped.lf_rho_q;
        let taps = shoebox_taps(&mapped, 48_000);
        self.early_d = [0.0; MAX_EARLY];
        self.early_tgt = self.early_now;
        for (i, tap) in taps.iter().take(MAX_EARLY).enumerate() {
            self.early_tgt[i] = tap.gain;
            self.early_d[i] = (tap.gain - self.early_now[i]) / steps;
        }
        if (mapped.t60 - old.t60).abs() > 1e-12 {
            self.fdn.start_decay(48_000, mapped.t60, SMOOTH);
        }
        let eq_changed = mapped.eq_gain_db != old.eq_gain_db;
        self.mapped = mapped;
        if (old.lf_shelf_hz - shelf_hz).abs() > 0.1
            || (old.lf_peak_hz - peak_hz).abs() > 0.1
            || (old.lf_peak_q - peak_q).abs() > 1e-6
            || (old.lf_rho_hz - rho_hz).abs() > 0.1
            || (old.lf_rho_q - rho_q).abs() > 1e-6
        {
            self.lf_shelf_applied = f64::NAN;
            self.lf_peak_applied = f64::NAN;
            self.rho_applied = f64::NAN;
        }
        let (head, shelf_k) = self.level_targets();
        self.headroom_tgt = head;
        self.headroom_d = (head - self.headroom_now) / steps;
        self.lf_k_tgt = shelf_k;
        self.lf_k_d = (shelf_k - self.lf_k_now) / steps;
        if !self.mapped.bypass
            && (eq_changed || self.fir.coeff.is_empty())
            && !self.mapped.eq_gain_db.is_empty()
        {
            let coeff = design_minphase(
                &self.mapped.eq_bands_hz,
                &self.mapped.eq_gain_db,
                48_000,
                EQ_TAPS,
                EQ_FFT,
            );
            if self.fir.coeff.is_empty() {
                self.fir.set_coeff(coeff);
                self.fir_tgt.clear();
            } else {
                self.fir_tgt = coeff;
            }
        } else {
            self.fir_tgt.clear();
        }
        self.retarget_hrir(&old);
        if leaving_bypass {
            self.bypass = false;
            self.xfade_left = SMOOTH;
            self.xfade_len = SMOOTH.max(1);
        } else {
            // A hard bypass would drop the latency and click. Keep the chain
            // and let the bed gains reach the bypass values.
            self.bypass = false;
        }
        self.smooth_left = SMOOTH;
    }

    fn retarget_hrir(&mut self, old: &MappedRouteV) {
        let spread = self.mapped.center_spread_deg;
        let distance_changed = (old.center_distance_m - self.mapped.center_distance_m).abs() > 1e-6;
        if (old.center_spread_deg - spread).abs() > 1e-6 || distance_changed {
            self.set_near_conv(CENTER_POS, spread, 0.0);
            self.set_near_conv(CENTER_NEG, -spread, 0.0);
        }
        if (old.height_el - self.mapped.height_el).abs() > 1e-6
            || old.height_az != self.mapped.height_az
        {
            let az = pad_height(&self.mapped.height_az);
            let el = self.mapped.height_el;
            for i in 0..N_HEIGHT {
                self.set_conv(H0 + i, az[i], el);
            }
        }
    }

    fn commit_smooth(&mut self, n: usize) {
        if self.smooth_left > 0 {
            let steps = n.min(self.smooth_left);
            for i in 0..N_GAINS {
                self.gain_now[i] += self.gain_d[i] * steps as f64;
            }
            for tap in 0..MAX_EARLY {
                self.early_now[tap] += self.early_d[tap] * steps as f64;
            }
            self.front_now += self.front_d * steps as f64;
            self.headroom_now += self.headroom_d * steps as f64;
            self.lf_k_now += self.lf_k_d * steps as f64;
            self.smooth_left -= steps;
            if self.smooth_left == 0 {
                self.gain_now = self.gain_tgt;
                self.early_now = self.early_tgt;
                self.front_now = self.front_tgt;
                self.headroom_now = self.headroom_tgt;
                self.lf_k_now = self.lf_k_tgt;
                self.gain_d = [0.0; N_GAINS];
                self.early_d = [0.0; MAX_EARLY];
                self.front_d = 0.0;
                self.headroom_d = 0.0;
                self.lf_k_d = 0.0;
                if !self.fir_tgt.is_empty() {
                    self.fir.copy_coeff_inplace(&self.fir_tgt);
                    self.fir_tgt.clear();
                }
                self.up.set_width_ambient(self.front_now, 1.0);
            }
        }
        self.xfade_left = self.xfade_left.saturating_sub(n);
    }

    fn process_block(&mut self, m: usize) {
        if self.bypass {
            self.mix_l[..m].copy_from_slice(&self.in_l[..m]);
            self.mix_r[..m].copy_from_slice(&self.in_r[..m]);
            self.played = true;
            return;
        }
        let stride = self.max_block;
        self.xo.process(
            &self.in_l[..m],
            &self.in_r[..m],
            &mut self.low_l[..m],
            &mut self.low_r[..m],
            &mut self.high_l[..m],
            &mut self.high_r[..m],
        );
        for i in 0..m {
            let gain = self.headroom_at(i);
            self.low_l[i] *= gain;
            self.low_r[i] *= gain;
            self.high_l[i] *= gain;
            self.high_r[i] *= gain;
        }
        if self.smooth_left > 0 && self.front_d.abs() > 0.0 {
            let mut done = 0;
            while done < m {
                let step = 64.min(m - done);
                let az = self.g_front(done);
                self.up.set_width_ambient(az, 1.0);
                self.up.process(
                    &self.high_l[done..done + step],
                    &self.high_r[done..done + step],
                    &mut self.stems,
                    stride,
                    done,
                );
                done += step;
            }
        } else {
            self.up.process(
                &self.high_l[..m],
                &self.high_r[..m],
                &mut self.stems,
                stride,
                0,
            );
        }
        self.mix_l[..m].fill(0.0);
        self.mix_r[..m].fill(0.0);

        let center = self.center_i;
        let center_row = (6 + center) * stride;
        self.conv[CENTER_POS].process(
            &self.stems[center_row..center_row + m],
            &mut self.tmp_l[..m],
            &mut self.tmp_r[..m],
        );
        acc(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );
        self.conv[CENTER_NEG].process(
            &self.stems[center_row..center_row + m],
            &mut self.tmp_l[..m],
            &mut self.tmp_r[..m],
        );
        acc(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );

        self.lateral[..m].fill(0.0);
        for k in 0..N_LATERAL {
            let stem = self.lateral_idx[k];
            let row = (6 + stem) * stride;
            for i in 0..m {
                self.lateral[i] += self.stems[row + i];
            }
            self.conv[LAT0 + k].process(
                &self.stems[row..row + m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            acc(
                &mut self.mix_l[..m],
                &mut self.mix_r[..m],
                &self.tmp_l[..m],
                &self.tmp_r[..m],
            );
        }

        let amb_l = 4 * stride;
        let amb_r = 5 * stride;
        self.dec_wide_l
            .process(&self.stems[amb_l..amb_l + m], &mut self.mono[..m]);
        self.conv[WIDE_L].process(&self.mono[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        self.acc_gain(G_WIDE, m);
        self.dec_wide_r
            .process(&self.stems[amb_r..amb_r + m], &mut self.mono[..m]);
        self.conv[WIDE_R].process(&self.mono[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        self.acc_gain(G_WIDE, m);

        self.delay_sur_l
            .process(&self.stems[amb_l..amb_l + m], &mut self.mono[..m]);
        self.dec_sur_l.process(&self.mono[..m], &mut self.feed[..m]);
        self.conv[SUR_L].process(&self.feed[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        self.acc_gain(G_SUR, m);
        self.delay_sur_r
            .process(&self.stems[amb_r..amb_r + m], &mut self.mono[..m]);
        self.dec_sur_r.process(&self.mono[..m], &mut self.feed[..m]);
        self.conv[SUR_R].process(&self.feed[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        self.acc_gain(G_SUR, m);

        for i in 0..m {
            let direct_lin = self.g(G_HDIRECT, i);
            let center_lin = self.g(G_CHEIGHT, i);
            self.mono[i] = self.lateral[i] * direct_lin + self.stems[center_row + i] * center_lin;
        }
        self.delay_lift
            .process(&self.mono[..m], &mut self.lifted[..m]);

        self.dec_height[0].process(&self.stems[amb_l..amb_l + m], &mut self.feed[..m]);
        for i in 0..m {
            self.mono[i] = self.g(G_HEIGHT, i) * (self.feed[i] + self.lifted[i]);
        }
        self.conv[H0].process(&self.mono[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );

        self.dec_height[1].process(&self.stems[amb_r..amb_r + m], &mut self.feed[..m]);
        for i in 0..m {
            self.mono[i] = self.g(G_HEIGHT, i) * (self.feed[i] + self.lifted[i]);
        }
        self.conv[H0 + 1].process(&self.mono[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );

        self.delay_height_l
            .process(&self.stems[amb_l..amb_l + m], &mut self.mono[..m]);
        self.dec_height[2].process(&self.mono[..m], &mut self.feed[..m]);
        for i in 0..m {
            self.feed[i] *= self.g(G_HEIGHT, i);
        }
        self.conv[H0 + 2].process(&self.feed[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );

        self.delay_height_r
            .process(&self.stems[amb_r..amb_r + m], &mut self.mono[..m]);
        self.dec_height[3].process(&self.mono[..m], &mut self.feed[..m]);
        for i in 0..m {
            self.feed[i] *= self.g(G_HEIGHT, i);
        }
        self.conv[H0 + 3].process(&self.feed[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );

        let dir_l = 0;
        let dir_r = stride;
        for i in 0..m {
            let direct = 0.5 * (self.stems[dir_l + i] + self.stems[dir_r + i]);
            let amb = 0.5 * (self.stems[amb_l + i] + self.stems[amb_r + i]);
            self.room[i] = direct * self.g(G_SEND, i) + amb;
        }
        let n_early = self
            .early
            .len()
            .min(self.delay_early.len())
            .min(self.conv.len().saturating_sub(EARLY0));
        for t in 0..n_early {
            self.delay_early[t].process(&self.room[..m], &mut self.mono[..m]);
            for i in 0..m {
                self.mono[i] *= self.early_g(t, i) * self.g(G_EARLY, i);
            }
            self.conv[EARLY0 + t].process(
                &self.mono[..m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            acc(
                &mut self.mix_l[..m],
                &mut self.mix_r[..m],
                &self.tmp_l[..m],
                &self.tmp_r[..m],
            );
        }
        self.fdn
            .process(&self.room[..m], &mut self.fdn_l[..m], &mut self.fdn_r[..m]);
        self.delay_fdn_l
            .process(&self.fdn_l[..m], &mut self.tmp_l[..m]);
        self.delay_fdn_r
            .process(&self.fdn_r[..m], &mut self.tmp_r[..m]);
        self.acc_gain(G_LATE, m);

        // Keep each ear's own low band. `(L+R)/2` is exact only when the
        // channels match; spaced-omni bass (ρ near 0 or negative) loses
        // several dB in that sum. Identical coefficients with separate
        // state leave L=R coherent and do not add delay. A slow peaking
        // boost, scaled by how far ρ sits below lf_rho_hi, fills the
        // upper low band on decorrelated hall recordings without lifting
        // mono bass.
        self.delay_low.process(&self.low_l[..m], &mut self.feed[..m]);
        self.delay_low_r
            .process(&self.low_r[..m], &mut self.mono[..m]);
        for i in 0..m {
            self.design_lf(self.lf_k_at(i));
            let shelved_l = biquad(&self.lf_sos, &mut self.lf_zi, self.feed[i]);
            let shaped_l = biquad(&self.lf_peak_sos, &mut self.lf_peak_zi, shelved_l);
            let shelved_r = biquad(&self.lf_sos, &mut self.lf_zi_r, self.mono[i]);
            let shaped_r = biquad(&self.lf_peak_sos, &mut self.lf_peak_zi_r, shelved_r);
            self.mix_l[i] += shaped_l;
            self.mix_r[i] += shaped_r;
        }

        if !self.fir.coeff.is_empty() {
            if self.fir_tgt.len() == self.fir.coeff.len() && self.smooth_left > 0 {
                for i in 0..m {
                    if self.smooth_left > i {
                        self.fir
                            .blend_toward(&self.fir_tgt, 1, self.smooth_left - i);
                    }
                    self.fir.process(
                        &self.mix_l[i..i + 1],
                        &self.mix_r[i..i + 1],
                        &mut self.tmp_l[i..i + 1],
                        &mut self.tmp_r[i..i + 1],
                    );
                }
            } else {
                self.fir.process(
                    &self.mix_l[..m],
                    &self.mix_r[..m],
                    &mut self.tmp_l[..m],
                    &mut self.tmp_r[..m],
                );
            }
            self.mix_l[..m].copy_from_slice(&self.tmp_l[..m]);
            self.mix_r[..m].copy_from_slice(&self.tmp_r[..m]);
        }
        // Decorrelation peak on the finished mix, so it can lift the
        // crossover region that never enters the low path. The detector
        // watches the low band. Both ears share coefficients.
        for i in 0..m {
            self.follow_rho(self.low_l[i], self.low_r[i], self.lf_k_at(i));
            self.mix_l[i] = biquad(&self.rho_sos, &mut self.rho_zi_l, self.mix_l[i]);
            self.mix_r[i] = biquad(&self.rho_sos, &mut self.rho_zi_r, self.mix_r[i]);
        }
        if self.xfade_left > 0 {
            let len = self.xfade_len.max(1) as f64;
            let left0 = self.xfade_left;
            for i in 0..m {
                let remain = left0.saturating_sub(i) as f64;
                let t = (1.0 - remain / len).clamp(0.0, 1.0);
                self.mix_l[i] = (1.0 - t) * self.in_l[i] + t * self.mix_l[i];
                self.mix_r[i] = (1.0 - t) * self.in_r[i] + t * self.mix_r[i];
            }
        }
        if self.limiter_on {
            self.limiter.process(
                &self.mix_l[..m],
                &self.mix_r[..m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            self.mix_l[..m].copy_from_slice(&self.tmp_l[..m]);
            self.mix_r[..m].copy_from_slice(&self.tmp_r[..m]);
        }
        self.commit_smooth(m);
        self.played = true;
    }

    fn level_targets(&self) -> (f64, f64) {
        if self.mapped.bypass {
            return (1.0, 0.0);
        }
        let scale = if self.bed_ref > 1e-12 {
            (bed_sum(&self.mapped) / self.bed_ref).max(0.0)
        } else {
            0.0
        };
        let head_lin = 10.0_f64.powf(-self.mapped.headroom_db * scale / 20.0);
        (head_lin, scale)
    }

    fn design_lf(&mut self, scale: f64) {
        let shelf_db = self.mapped.lf_shelf_db * scale;
        let peak_db = self.mapped.lf_peak_db * scale;
        if (shelf_db - self.lf_shelf_applied).abs() < 1e-4
            && (peak_db - self.lf_peak_applied).abs() < 1e-4
        {
            return;
        }
        self.lf_sos = low_shelf_coeffs(self.mapped.lf_shelf_hz, shelf_db, 48_000.0);
        self.lf_peak_sos = peak_coeffs(
            self.mapped.lf_peak_hz,
            peak_db,
            self.mapped.lf_peak_q,
            48_000.0,
        );
        self.lf_shelf_applied = shelf_db;
        self.lf_peak_applied = peak_db;
    }

    fn arm_rho(&mut self) {
        let tau = self.mapped.lf_rho_tau_s.max(0.2);
        self.rho_a = (-1.0 / (tau * 48_000.0)).exp();
        // 4 dB/s. Correlation can move; the peak must not.
        self.rho_slew = 4.0 / 48_000.0;
        let fc = 80.0_f64;
        self.rho_hp_a = (-2.0 * std::f64::consts::PI * fc / 48_000.0).exp();
        self.reset_rho();
    }

    fn reset_rho(&mut self) {
        self.rho_pl = 0.0;
        self.rho_pr = 0.0;
        self.rho_c = 0.0;
        self.rho_hp_yl = 0.0;
        self.rho_hp_yr = 0.0;
        self.rho_hp_xl = 0.0;
        self.rho_hp_xr = 0.0;
        self.rho_db_now = 0.0;
        self.rho_applied = f64::NAN;
        self.rho_sos = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
        self.rho_zi_l = [0.0; 2];
        self.rho_zi_r = [0.0; 2];
    }

    fn follow_rho(&mut self, left: f64, right: f64, scale: f64) {
        let hl = one_pole_hp(&mut self.rho_hp_yl, &mut self.rho_hp_xl, left, self.rho_hp_a);
        let hr = one_pole_hp(&mut self.rho_hp_yr, &mut self.rho_hp_xr, right, self.rho_hp_a);
        let a = self.rho_a;
        let b = 1.0 - a;
        self.rho_pl = a * self.rho_pl + b * hl * hl;
        self.rho_pr = a * self.rho_pr + b * hr * hr;
        self.rho_c = a * self.rho_c + b * hl * hr;
        let den = (self.rho_pl * self.rho_pr).sqrt();
        let rho = if den > 1e-12 {
            (self.rho_c / den).clamp(-1.0, 1.0)
        } else {
            1.0
        };
        let span = (self.mapped.lf_rho_hi - self.mapped.lf_rho_lo).max(0.05);
        let t = ((self.mapped.lf_rho_hi - rho) / span).clamp(0.0, 1.0);
        let target = self.mapped.lf_rho_db * t * scale;
        let step = (target - self.rho_db_now).clamp(-self.rho_slew, self.rho_slew);
        self.rho_db_now += step;
        if self.rho_applied.is_nan() || (self.rho_db_now - self.rho_applied).abs() > 0.02 {
            self.rho_sos = peak_coeffs(
                self.mapped.lf_rho_hz,
                self.rho_db_now,
                self.mapped.lf_rho_q,
                48_000.0,
            );
            self.rho_applied = self.rho_db_now;
        }
    }

    fn headroom_db_value(&self) -> f64 {
        if self.mapped.bypass {
            return 0.0;
        }
        let (lin, _) = self.level_targets();
        -20.0 * lin.max(1e-12).log10()
    }

    fn headroom_at(&self, sample: usize) -> f64 {
        if self.smooth_left == 0 {
            return self.headroom_now;
        }
        self.headroom_now + self.headroom_d * sample.min(self.smooth_left) as f64
    }

    fn lf_k_at(&self, sample: usize) -> f64 {
        if self.smooth_left == 0 {
            return self.lf_k_now;
        }
        self.lf_k_now + self.lf_k_d * sample.min(self.smooth_left) as f64
    }
}

fn bed_sum(mapped: &MappedRouteV) -> f64 {
    mapped.wide_lin + mapped.surround_lin + mapped.height_lin + mapped.early_lin + mapped.late_lin
}

fn biquad(sos: &[f64; 6], state: &mut [f64; 2], x: f64) -> f64 {
    let yn = sos[0] * x + state[0];
    state[0] = sos[1] * x - sos[4] * yn + state[1];
    state[1] = sos[2] * x - sos[5] * yn;
    yn
}

fn one_pole_hp(y: &mut f64, prev: &mut f64, x: f64, a: f64) -> f64 {
    let out = a * (*y + x - *prev);
    *y = out;
    *prev = x;
    out
}

fn color_bed(ir: &mut [f64], hz: f64, db: f64, q: f64) {
    if db.abs() < 0.05 {
        return;
    }
    let sos = peak_coeffs(hz, db, q, 48_000.0);
    let mut zi = [0.0; 2];
    for sample in ir.iter_mut() {
        *sample = biquad(&sos, &mut zi, *sample);
    }
}

/// RBJ low shelf, S = 1. Coefficients are `[b0, b1, b2, 1, a1, a2]`.
fn low_shelf_coeffs(fc: f64, gain_db: f64, sample_rate: f64) -> [f64; 6] {
    if gain_db.abs() < 1e-6 {
        return [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
    }
    let a = 10.0_f64.powf(gain_db / 40.0);
    let w0 = 2.0 * std::f64::consts::PI * fc / sample_rate;
    let cos_w = w0.cos();
    let alpha = w0.sin() / std::f64::consts::SQRT_2;
    let a_sqrt = a.sqrt();
    let two_sqrt = 2.0 * a_sqrt * alpha;
    let b0 = a * ((a + 1.0) - (a - 1.0) * cos_w + two_sqrt);
    let b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cos_w);
    let b2 = a * ((a + 1.0) - (a - 1.0) * cos_w - two_sqrt);
    let a0 = (a + 1.0) + (a - 1.0) * cos_w + two_sqrt;
    let a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cos_w);
    let a2 = (a + 1.0) + (a - 1.0) * cos_w - two_sqrt;
    [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]
}

/// RBJ peaking EQ. Coefficients are `[b0, b1, b2, 1, a1, a2]`.
fn peak_coeffs(fc: f64, gain_db: f64, q: f64, sample_rate: f64) -> [f64; 6] {
    if gain_db.abs() < 1e-6 {
        return [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];
    }
    let a = 10.0_f64.powf(gain_db / 40.0);
    let w0 = 2.0 * std::f64::consts::PI * fc / sample_rate;
    let alpha = w0.sin() / (2.0 * q.max(0.1));
    let cos_w = w0.cos();
    let b0 = 1.0 + alpha * a;
    let b1 = -2.0 * cos_w;
    let b2 = 1.0 - alpha * a;
    let a0 = 1.0 + alpha / a;
    let a1 = -2.0 * cos_w;
    let a2 = 1.0 - alpha / a;
    [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]
}

fn gain_pack(mapped: &MappedRouteV) -> [f64; N_GAINS] {
    let mut gain = [0.0; N_GAINS];
    gain[G_WIDE] = mapped.wide_lin;
    gain[G_SUR] = mapped.surround_lin;
    gain[G_HEIGHT] = mapped.height_lin;
    gain[G_EARLY] = mapped.early_lin;
    gain[G_LATE] = mapped.late_lin;
    gain[G_HDIRECT] = mapped.height_direct_lin;
    gain[G_CHEIGHT] = mapped.center_height_lin;
    gain[G_SEND] = mapped.direct_room_send_lin;
    gain
}

fn apply_upmix_controls(up: &mut StreamingUpmix, mapped: &MappedRouteV) {
    up.set_mask_controls(
        mapped.coherence_floor,
        mapped.coherence_power,
        mapped.pan_gamma,
        mapped.direct_bleed,
        mapped.hf_pan_floor,
        mapped.hf_pan_start_hz,
        mapped.hf_pan_end_hz,
        mapped.hf_direct_start_hz,
        mapped.hf_direct_end_hz,
    );
    up.enable_steady_cola();
}

fn polarity(sample_rate: u32, seed: u64, delays_ms: &[f64]) -> PolarityDelay {
    let table: &[f64] = if delays_ms.is_empty() {
        &[
            3.2, 6.4, 10.0, 13.5, 4.4, 7.6, 11.2, 14.8, 3.8, 6.0, 9.2, 12.4,
        ]
    } else {
        delays_ms
    };
    let ms = table[(seed as usize) % table.len()];
    let samples = ms_samples(ms, sample_rate);
    let sign = if (seed / 7).is_multiple_of(2) {
        -1.0
    } else {
        1.0
    };
    PolarityDelay::new(samples.max(1), sign)
}

fn ms_samples(ms: f64, sample_rate: u32) -> usize {
    numpy_round(ms * 1e-3 * f64::from(sample_rate)).max(0.0) as usize
}

fn center_index(az: &[f64; N_SECTORS]) -> usize {
    let mut best_i = 0;
    let mut best = f64::MAX;
    for (i, az) in az.iter().enumerate() {
        let mag = az.abs();
        if mag < best {
            best = mag;
            best_i = i;
        }
    }
    best_i
}

fn pad_height(az: &[f64]) -> [f64; N_HEIGHT] {
    let defaults = [45.0, -45.0, 135.0, -135.0];
    let mut out = defaults;
    for (i, value) in az.iter().take(N_HEIGHT).enumerate() {
        out[i] = *value;
    }
    out
}

fn acc(dst_l: &mut [f64], dst_r: &mut [f64], src_l: &[f64], src_r: &[f64]) {
    for i in 0..dst_l.len() {
        dst_l[i] += src_l[i];
        dst_r[i] += src_r[i];
    }
}

fn nearfield_db(
    az: f64,
    el: f64,
    distance_m: f64,
    ref_m: f64,
    ild_scale: f64,
    lf_max_db: f64,
) -> (f64, f64) {
    let src = az_el_to_cart(az, el, distance_m.max(0.25));
    let d_l = norm3(sub3(src, [0.0, 0.090, 0.0])).max(1e-3);
    let d_r = norm3(sub3(src, [0.0, -0.090, 0.0])).max(1e-3);
    let g_l = distance_m / d_l;
    let g_r = distance_m / d_r;
    let mean = (g_l * g_r).max(1e-12).sqrt();
    let ild_l = ild_scale * 20.0 * (g_l / mean).log10();
    let ild_r = ild_scale * 20.0 * (g_r / mean).log10();
    let mut lf = 0.0;
    if distance_m < ref_m {
        lf = lf_max_db.min(20.0 * (ref_m / distance_m).log10());
    }
    (lf + ild_l, lf + ild_r)
}

fn sub3(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn norm3(v: [f64; 3]) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

fn round_dp(x: f64, dp: i32) -> f64 {
    let scale = 10.0_f64.powi(dp);
    numpy_round(x * scale) / scale
}

fn apply_low_shelf(h: &mut [f64], tmp: &mut [f64], sr: f64, hz: f64, gain_db: f64) {
    if gain_db.abs() < 0.05 {
        return;
    }
    let sos = butterworth2_low(hz, sr);
    apply_sos(&sos, h, tmp);
    let k = 10.0_f64.powf(gain_db / 20.0) - 1.0;
    for i in 0..h.len() {
        h[i] += k * tmp[i];
    }
}

fn shoebox_taps(mapped: &MappedRouteV, sample_rate: u32) -> Vec<EarlyTap> {
    let source = mapped.source_m;
    let listener = mapped.listener_m;
    let size = mapped.room_size_m;
    let absorption = mapped.absorption;
    let n_keep = mapped.early_taps;
    let ceiling_db = mapped.ceiling_db;
    let floor_db = mapped.floor_db;
    let mut raw: Vec<(f64, f64, f64, f64)> = Vec::new();
    let order = mapped.early_order.max(0);
    for nx in -order..=order {
        for ny in -order..=order {
            for nz in -order..=order {
                if nx == 0 && ny == 0 && nz == 0 {
                    continue;
                }
                let image = [
                    image_coord(source[0], nx, size[0]),
                    image_coord(source[1], ny, size[1]),
                    image_coord(source[2], nz, size[2]),
                ];
                let vec = sub3(image, listener);
                let (az, el, dist) = cart_to_az_el(vec);
                if dist < 1e-6 {
                    continue;
                }
                let bounces = nx.unsigned_abs() + ny.unsigned_abs() + nz.unsigned_abs();
                let gain = (1.0 - absorption).powi(bounces as i32) / dist;
                raw.push((dist / 343.0, gain, az, el));
            }
        }
    }
    raw.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    let chosen = reserve_vertical(&raw, n_keep);
    let sr = f64::from(sample_rate);
    chosen
        .into_iter()
        .map(|(delay_s, gain, az, el)| {
            let boost = if el >= 25.0 {
                10.0_f64.powf(ceiling_db / 20.0)
            } else if el <= -25.0 {
                10.0_f64.powf(floor_db / 20.0)
            } else {
                1.0
            };
            EarlyTap {
                delay: numpy_round(delay_s * sr).max(0.0) as usize,
                gain: gain * boost,
                azimuth_deg: az,
                elevation_deg: el,
            }
        })
        .collect()
}

fn image_coord(src: f64, n: i32, length: f64) -> f64 {
    if n % 2 == 0 {
        f64::from(n) * length + src
    } else {
        f64::from(n) * length + (length - src)
    }
}

fn reserve_vertical(taps: &[(f64, f64, f64, f64)], n_keep: usize) -> Vec<(f64, f64, f64, f64)> {
    if n_keep == 0 || taps.len() <= n_keep {
        return taps.to_vec();
    }
    let mut ceiling_i = 0usize;
    let mut floor_i = 0usize;
    for i in 1..taps.len() {
        if taps[i].3 > taps[ceiling_i].3 {
            ceiling_i = i;
        }
        if taps[i].3 < taps[floor_i].3 {
            floor_i = i;
        }
    }
    let mut chosen: Vec<usize> = Vec::new();
    for i in [ceiling_i, floor_i] {
        if taps[i].3.abs() >= 20.0 && !chosen.contains(&i) {
            chosen.push(i);
        }
    }
    for i in 0..taps.len() {
        if chosen.contains(&i) {
            continue;
        }
        chosen.push(i);
        if chosen.len() >= n_keep {
            break;
        }
    }
    chosen.sort_by(|&a, &b| {
        taps[a]
            .0
            .partial_cmp(&taps[b].0)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    chosen.into_iter().map(|i| taps[i]).collect()
}

fn design_minphase(
    bands: &[f64],
    gains: &[f64],
    sample_rate: u32,
    taps: usize,
    nfft: usize,
) -> Vec<f64> {
    let mut planner = RealFftPlanner::<f64>::new();
    let forward = planner.plan_fft_forward(nfft);
    let inverse = planner.plan_fft_inverse(nfft);
    let bins = nfft / 2 + 1;
    let mut spec = vec![Complex::new(0.0, 0.0); bins];
    let sr = f64::from(sample_rate);
    for i in 0..bins {
        let freq = i as f64 * sr / nfft as f64;
        let gdb = interp(freq, bands, gains) * fade_lo(freq) * fade_hi(freq);
        let lin = 10.0_f64.powf(gdb / 20.0).max(1e-8);
        spec[i] = Complex::new(lin.ln(), 0.0);
    }
    let mut time = vec![0.0; nfft];
    let mut scratch_inv = inverse.make_scratch_vec();
    inverse
        .process_with_scratch(&mut spec, &mut time, &mut scratch_inv)
        .expect("eq inverse fft");
    let scale = 1.0 / nfft as f64;
    for sample in &mut time {
        *sample *= scale;
    }
    let half = nfft / 2;
    for i in 1..half {
        time[i] *= 2.0;
    }
    for sample in time.iter_mut().skip(half + 1) {
        *sample = 0.0;
    }
    let mut scratch_fwd = forward.make_scratch_vec();
    forward
        .process_with_scratch(&mut time, &mut spec, &mut scratch_fwd)
        .expect("eq forward fft");
    for bin in &mut spec {
        *bin = bin.exp();
    }
    inverse
        .process_with_scratch(&mut spec, &mut time, &mut scratch_inv)
        .expect("eq inverse fft");
    for sample in &mut time {
        *sample *= scale;
    }
    let m = 2 * taps;
    let mut fir = vec![0.0; taps];
    for i in 0..taps {
        let n = (taps + i) as f64;
        let window = 0.5 - 0.5 * (2.0 * std::f64::consts::PI * n / (m as f64 - 1.0)).cos();
        fir[i] = time[i] * window;
    }
    fir
}

fn fade_lo(freq: f64) -> f64 {
    ((freq - 20.0) / 20.0).clamp(0.0, 1.0)
}

fn fade_hi(freq: f64) -> f64 {
    ((18_000.0 - freq) / 2000.0).clamp(0.0, 1.0)
}

fn interp(x: f64, xp: &[f64], fp: &[f64]) -> f64 {
    if xp.is_empty() || fp.is_empty() || xp.len() != fp.len() {
        return 0.0;
    }
    if x <= xp[0] || x >= xp[xp.len() - 1] {
        return 0.0;
    }
    let mut i = 0;
    while i + 1 < xp.len() && xp[i + 1] < x {
        i += 1;
    }
    let span = xp[i + 1] - xp[i];
    if span <= 0.0 {
        return fp[i];
    }
    let t = (x - xp[i]) / span;
    (1.0 - t) * fp[i] + t * fp[i + 1]
}

fn resolve_hrtf_name(explicit: &str, pack: &std::path::Path, source: &std::path::Path) -> String {
    if !explicit.is_empty() {
        return explicit.to_string();
    }
    let blob = format!(
        "{} {}",
        pack.to_string_lossy().to_lowercase(),
        source.to_string_lossy().to_lowercase()
    );
    if blob.contains("sadie") {
        "sadie".to_string()
    } else if blob.contains("ku100") {
        "ku100".to_string()
    } else {
        String::new()
    }
}

fn finite(x: f32) -> f32 {
    if x.is_finite() {
        x
    } else {
        0.0
    }
}

fn finite32(x: f64) -> f32 {
    if x.is_finite() {
        x as f32
    } else {
        0.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hrir::{sha256_file, MAGIC, VERSION};
    use crate::preset_v::RouteVTable;
    use crate::processor::Params;
    use std::io::Write;
    use std::path::PathBuf;

    fn write_pack(dir: &std::path::Path) -> (PathBuf, PathBuf, PathBuf) {
        let source = dir.join("source.bin");
        let pack_path = dir.join("tiny.ahrp");
        let preset = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../presets/route_v.toml");
        std::fs::write(&source, b"synthetic-sofa").unwrap();
        let hash = sha256_file(&source).unwrap();
        let mut b = Vec::new();
        b.extend_from_slice(MAGIC);
        b.extend_from_slice(&VERSION.to_le_bytes());
        b.extend_from_slice(&48_000u32.to_le_bytes());
        b.extend_from_slice(&1u32.to_le_bytes());
        let taps = 8u32;
        b.extend_from_slice(&taps.to_le_bytes());
        let eq = b"frontal+power";
        b.extend_from_slice(&(eq.len() as u32).to_le_bytes());
        b.extend_from_slice(eq);
        b.extend_from_slice(&hash);
        for v in [0.0f64, 0.0, 0.0, 0.0] {
            b.extend_from_slice(&v.to_le_bytes());
        }
        for rep in 0..4 {
            for t in 0..taps {
                let s = if rep >= 2 && t == 0 { 1.0f32 } else { 0.0 };
                b.extend_from_slice(&s.to_le_bytes());
            }
        }
        let mut f = std::fs::File::create(&pack_path).unwrap();
        f.write_all(&b).unwrap();
        (pack_path, source, preset)
    }

    fn prepared(max_block: usize, strength: f64, preset: &str) -> RouteV {
        let dir =
            std::env::temp_dir().join(format!("aurora_route_v_{preset}_{strength}_{max_block}"));
        let _ = std::fs::create_dir_all(&dir);
        let (pack, source, preset_path) = write_pack(&dir);
        let mut cfg = RenderConfig::route_v(&pack, &source, &preset_path);
        cfg.strength = strength;
        cfg.preset = preset.to_string();
        cfg.hrtf_name = "sadie".to_string();
        let mut dsp = RouteV::new();
        dsp.prepare(48_000, max_block, &cfg).unwrap();
        dsp
    }

    fn render(dsp: &mut RouteV, left: &[f32], right: &[f32], block: usize) -> Vec<f32> {
        let n = left.len();
        let mut out_l = vec![0.0; n];
        let mut out_r = vec![0.0; n];
        let mut o = 0;
        while o < n {
            let m = block.min(n - o);
            dsp.process(
                &[&left[o..o + m], &right[o..o + m]],
                &mut [&mut out_l[o..o + m], &mut out_r[o..o + m]],
                m,
            );
            o += m;
        }
        let mut stereo = Vec::with_capacity(n * 2);
        for i in 0..n {
            stereo.push(out_l[i]);
            stereo.push(out_r[i]);
        }
        stereo
    }

    #[test]
    fn shoebox_matches_prototype_taps() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../presets/route_v.toml");
        let table = RouteVTable::load(&path).unwrap();
        let mapped = map_route_v(
            1.0,
            table.get("V2").unwrap(),
            "sadie",
            &table.delays_ms,
            &table.strength,
        );
        let taps = shoebox_taps(&mapped, 48_000);
        assert_eq!(taps.len(), 12);
        assert_eq!(taps[0].delay, 505);
        assert!(
            (taps[0].gain - 0.216_249_919_471).abs() < 1e-9,
            "{}",
            taps[0].gain
        );
        assert!(taps[0].azimuth_deg.abs() < 1e-6);
        assert!((taps[0].elevation_deg - (-43.876_697_29)).abs() < 1e-4);
        assert_eq!(taps[1].delay, 545);
        let ceiling = 10.0_f64.powf(4.0 / 20.0);
        assert!(
            (taps[1].gain - 0.200_263_504_726 * ceiling).abs() < 1e-8,
            "{}",
            taps[1].gain
        );
        assert!((taps[1].elevation_deg - 48.122_130_46).abs() < 1e-4);
        assert_eq!(taps[8].delay, 980);
        assert_eq!(taps[9].delay, 980);
    }

    #[test]
    fn nearfield_matches_prototype() {
        let (l, r) = nearfield_db(28.0, 0.0, 0.9, 1.5, 1.0, 4.0);
        assert!((l - 4.404_909_582_103_278).abs() < 1e-9, "{l}");
        assert!((r - 3.595_090_417_896_722_5).abs() < 1e-9, "{r}");
        assert!((round_dp(l, 2) - 4.40).abs() < 1e-12);
        assert!((round_dp(r, 2) - 3.60).abs() < 1e-12);
    }

    #[test]
    fn fixed_eq_matches_prototype_fir() {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../presets/route_v.toml");
        let table = RouteVTable::load(&path).unwrap();
        let mapped = map_route_v(
            1.0,
            table.get("V2").unwrap(),
            "sadie",
            &table.delays_ms,
            &table.strength,
        );
        let fir = design_minphase(
            &mapped.eq_bands_hz,
            &mapped.eq_gain_db,
            48_000,
            EQ_TAPS,
            EQ_FFT,
        );
        assert!(
            (fir[0] - 0.972_073_295_479_075_5).abs() < 1e-8,
            "{}",
            fir[0]
        );
        assert!(
            (fir[1] - (-0.059_735_598_410_645_77)).abs() < 1e-8,
            "{}",
            fir[1]
        );
        let sum: f64 = fir.iter().sum();
        assert!((sum - 1.055_990_112_627_538_8).abs() < 1e-8, "{sum}");
        let energy: f64 = fir.iter().map(|v| v * v).sum();
        assert!((energy - 0.962_324_694_557_408_9).abs() < 1e-8, "{energy}");
    }

    #[test]
    fn headroom_follows_strength_and_bypass_stays_exact() {
        let on = prepared(128, 1.0, "V3");
        let db = on.headroom_db();
        assert!(db > 5.0 && db < 24.0, "{db}");
        let off = prepared(128, 0.0, "V3");
        assert_eq!(off.headroom_db(), 0.0);
    }

    #[test]
    fn strength_zero_bypasses() {
        let mut dsp = prepared(256, 0.0, "V2");
        assert_eq!(dsp.latency_samples(), 0);
        let n = 64;
        let left: Vec<f32> = (0..n).map(|i| (i as f32) * 0.01).collect();
        let right: Vec<f32> = (0..n).map(|i| 0.2 - i as f32 * 0.003).collect();
        let mut ol = vec![0.0; n];
        let mut or_ = vec![0.0; n];
        dsp.process(&[&left, &right], &mut [&mut ol, &mut or_], n);
        assert_eq!(ol, left);
        assert_eq!(or_, right);
    }

    #[test]
    fn low_band_keeps_anticorrelated_energy_and_mono_stays_coherent() {
        let n = 48_000 * 2;
        let freq = 60.0_f32;
        let mut left = vec![0.0f32; n];
        let mut right_mono = vec![0.0f32; n];
        let mut right_anti = vec![0.0f32; n];
        for i in 0..n {
            let s = 0.2 * (2.0 * std::f32::consts::PI * freq * i as f32 / 48_000.0).sin();
            left[i] = s;
            right_mono[i] = s;
            right_anti[i] = -s;
        }
        let mut mono = prepared(256, 1.0, "V3");
        let latency = mono.latency_samples();
        let mono_out = render(&mut mono, &left, &right_mono, 256);
        let mut anti = prepared(256, 1.0, "V3");
        let anti_out = render(&mut anti, &left, &right_anti, 256);
        let start = latency + 4_000;
        let end = n - 2_000;
        let (ml, mr) = split_range(&mono_out, start, end);
        let (al, ar) = split_range(&anti_out, start, end);
        let corr = correlation(&ml, &mr);
        assert!(corr > 0.98, "mono low-band correlation {corr}");
        let mono_db = tone_db(&ml, freq);
        let anti_db = tone_db(&al, freq);
        assert!(
            anti_db + 3.0 > mono_db,
            "60 Hz mono {mono_db:.2} dB vs anti-phase {anti_db:.2} dB"
        );
        let anti_r = tone_db(&ar, freq);
        assert!(
            (anti_db - anti_r).abs() < 0.5,
            "anti-phase ears {anti_db:.2} / {anti_r:.2}"
        );
    }

    fn split_range(interleaved: &[f32], start: usize, end: usize) -> (Vec<f32>, Vec<f32>) {
        let mut left = Vec::with_capacity(end - start);
        let mut right = Vec::with_capacity(end - start);
        for i in start..end {
            left.push(interleaved[i * 2]);
            right.push(interleaved[i * 2 + 1]);
        }
        (left, right)
    }

    fn correlation(a: &[f32], b: &[f32]) -> f64 {
        let n = a.len().min(b.len());
        let mut mean_a = 0.0;
        let mut mean_b = 0.0;
        for i in 0..n {
            mean_a += f64::from(a[i]);
            mean_b += f64::from(b[i]);
        }
        mean_a /= n as f64;
        mean_b /= n as f64;
        let mut num = 0.0;
        let mut da = 0.0;
        let mut db = 0.0;
        for i in 0..n {
            let xa = f64::from(a[i]) - mean_a;
            let xb = f64::from(b[i]) - mean_b;
            num += xa * xb;
            da += xa * xa;
            db += xb * xb;
        }
        num / (da * db).sqrt().max(1e-30)
    }

    fn tone_db(x: &[f32], freq: f32) -> f64 {
        let mut re = 0.0;
        let mut im = 0.0;
        for (i, sample) in x.iter().enumerate() {
            let w = 2.0 * std::f64::consts::PI * f64::from(freq) * i as f64 / 48_000.0;
            re += f64::from(*sample) * w.cos();
            im += f64::from(*sample) * w.sin();
        }
        let power = (re * re + im * im) / (x.len() as f64 * x.len() as f64);
        10.0 * power.max(1e-30).log10()
    }

    #[test]
    fn block_sizes_agree_below_minus_100_dbfs() {
        let n = 4096;
        let left: Vec<f32> = (0..n).map(|i| 0.05 * (i as f32 * 0.07).sin()).collect();
        let right: Vec<f32> = (0..n)
            .map(|i| 0.04 * (i as f32 * 0.05 + 0.4).sin())
            .collect();
        let reference = {
            let mut dsp = prepared(n, 1.0, "V2");
            render(&mut dsp, &left, &right, n)
        };
        for block in [32usize, 64, 256, 1024, 4096] {
            let mut dsp = prepared(n, 1.0, "V2");
            let got = render(&mut dsp, &left, &right, block);
            for (u, v) in got.iter().zip(reference.iter()) {
                let db = 20.0 * ((*u - *v).abs() as f64).max(1e-20).log10();
                assert!(db <= -100.0, "block {block} {db}");
            }
        }
    }

    #[test]
    fn process_does_not_allocate_and_nan_is_finite() {
        let mut dsp = prepared(512, 1.0, "V2");
        let n = 256;
        let mut left = vec![0.1f32; n];
        left[3] = f32::NAN;
        left[8] = f32::INFINITY;
        let right = vec![0.05f32; n];
        let mut ol = vec![0.0; n];
        let mut or_ = vec![0.0; n];
        crate::test_alloc::set_enabled(false);
        crate::test_alloc::COUNT.store(0, std::sync::atomic::Ordering::Relaxed);
        crate::test_alloc::set_enabled(true);
        dsp.process(&[&left, &right], &mut [&mut ol, &mut or_], n);
        let allocs = crate::test_alloc::COUNT.load(std::sync::atomic::Ordering::Relaxed);
        crate::test_alloc::set_enabled(false);
        assert_eq!(allocs, 0, "process allocated {allocs} times");
        assert!(ol.iter().chain(or_.iter()).all(|s| s.is_finite()));
        dsp.reset();
        let mut ol2 = vec![0.0; n];
        let mut or2 = vec![0.0; n];
        dsp.process(&[&left, &right], &mut [&mut ol2, &mut or2], n);
        assert_eq!(ol, ol2);
        assert_eq!(or_, or2);
    }

    #[test]
    fn mid_stream_controls_do_not_step_or_allocate() {
        let mut dsp = prepared(256, 1.0, "V3");
        let n = 8192;
        let split = 4096;
        let left: Vec<f32> = (0..n)
            .map(|i| 0.15 * (i as f32 * 2.0 * std::f32::consts::PI * 200.0 / 48_000.0).sin())
            .collect();
        let right = left.clone();
        let mut out_l = vec![0.0f32; n];
        let mut out_r = vec![0.0f32; n];
        let mut offset = 0;
        while offset < split {
            let m = 256.min(split - offset);
            dsp.process(
                &[&left[offset..offset + m], &right[offset..offset + m]],
                &mut [
                    &mut out_l[offset..offset + m],
                    &mut out_r[offset..offset + m],
                ],
                m,
            );
            offset += m;
        }
        dsp.set_params(&Params {
            strength: 0.35,
            preset: "V3".into(),
            seed: 20_261_016,
            width: Some(1.0),
            height: Some(0.0),
            room: Some(1.0),
            voice_distance: Some(1.0),
        });
        crate::test_alloc::set_enabled(false);
        crate::test_alloc::COUNT.store(0, std::sync::atomic::Ordering::Relaxed);
        crate::test_alloc::set_enabled(true);
        while offset < n {
            let m = 256.min(n - offset);
            dsp.process(
                &[&left[offset..offset + m], &right[offset..offset + m]],
                &mut [
                    &mut out_l[offset..offset + m],
                    &mut out_r[offset..offset + m],
                ],
                m,
            );
            offset += m;
        }
        let allocs = crate::test_alloc::COUNT.load(std::sync::atomic::Ordering::Relaxed);
        crate::test_alloc::set_enabled(false);
        let interleaved: Vec<f32> = out_l
            .iter()
            .zip(out_r.iter())
            .flat_map(|(l, r)| [*l, *r])
            .collect();
        assert_eq!(allocs, 0, "smooth process allocated {allocs} times");
        assert!(interleaved.iter().all(|s| s.is_finite()));
        assert!(interleaved.iter().all(|s| s.abs() <= 1.0));
        let (peak_before, _) = channel_step_at(&interleaved, 0, split);
        let (peak_after, at) = channel_step_at(&interleaved, split, n);
        assert!(
            peak_after < peak_before.max(0.02) * 8.0,
            "step {peak_after} at sample {at} vs steady {peak_before}"
        );
    }

    fn channel_step_at(interleaved: &[f32], start: usize, end: usize) -> (f32, usize) {
        let mut peak = 0.0f32;
        let mut at = start;
        let end = end.min(interleaved.len() / 2);
        for channel in 0..2 {
            let mut frame = start.max(1);
            while frame < end {
                let index = frame * 2 + channel;
                let prev = (frame - 1) * 2 + channel;
                let step = (interleaved[index] - interleaved[prev]).abs();
                if step > peak {
                    peak = step;
                    at = frame;
                }
                frame += 1;
            }
        }
        (peak, at)
    }
}
