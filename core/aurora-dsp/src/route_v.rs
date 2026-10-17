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
//! partition so the mix lines up. `process` does not allocate.

use crate::conv::{DelayLine, UniformConvolver};
use crate::coords::{az_el_to_cart, cart_to_az_el};
use crate::crossover::{apply_sos, butterworth2_low, Lr4Crossover};
use crate::error::DspError;
use crate::fdn::FdnReverb;
use crate::hrir::HrirPack;
use crate::interpolate::idw_render;
use crate::limiter::{TruePeakLimiter, LOOKAHEAD};
use crate::preset_v::{map_route_v, MappedRouteV, RouteVTable};
use crate::processor::{Params, Processor, RenderConfig};
use crate::rng::numpy_round;
use crate::upmix::{StreamingUpmix, N_FFT, N_SECTORS, N_STEMS};
use num_complex::Complex;
use realfft::RealFftPlanner;

const PART: usize = 64;
const FADE: usize = 128;
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
    delay_fdn_l: DelayLine,
    delay_fdn_r: DelayLine,
    conv: Vec<UniformConvolver>,
    early: Vec<EarlyTap>,
    fdn: FdnReverb,
    fir: FirStereo,
    limiter: TruePeakLimiter,
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
        let mapped = map_route_v(config.strength, &row, &hrtf_name, &table.delays_ms);
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
            delay_fdn_l: DelayLine::new(PART),
            delay_fdn_r: DelayLine::new(PART),
            conv,
            early: Vec::new(),
            fdn: FdnReverb::new(sample_rate, row.t60, config.seed),
            fir: FirStereo::empty(),
            // Detector ceiling sits ~0.7 dB under scipy's 4× resample, so an
            // internal −1.8 dBTP still measures at or under −1 dBTP.
            limiter: TruePeakLimiter::with_ceiling(sample_rate, -1.8),
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
        };
        engine.install(sample_rate);
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
        let mapped = map_route_v(
            params.strength,
            &row,
            &engine.hrtf_name,
            &engine.table.delays_ms,
        );
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
        engine.delay_fdn_l.reset();
        engine.delay_fdn_r.reset();
        for conv in &mut engine.conv {
            conv.reset();
        }
        engine.fdn.reset();
        engine.fir.reset();
        engine.limiter.reset();
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

    fn process_block(&mut self, m: usize) {
        if self.bypass {
            self.mix_l[..m].copy_from_slice(&self.in_l[..m]);
            self.mix_r[..m].copy_from_slice(&self.in_r[..m]);
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
        self.up.process(
            &self.high_l[..m],
            &self.high_r[..m],
            &mut self.stems,
            stride,
            0,
        );
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
        let wide_lin = self.mapped.wide_lin;
        self.dec_wide_l
            .process(&self.stems[amb_l..amb_l + m], &mut self.mono[..m]);
        self.conv[WIDE_L].process(&self.mono[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc_scaled(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
            wide_lin,
        );
        self.dec_wide_r
            .process(&self.stems[amb_r..amb_r + m], &mut self.mono[..m]);
        self.conv[WIDE_R].process(&self.mono[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc_scaled(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
            wide_lin,
        );

        let surround_lin = self.mapped.surround_lin;
        self.delay_sur_l
            .process(&self.stems[amb_l..amb_l + m], &mut self.mono[..m]);
        self.dec_sur_l.process(&self.mono[..m], &mut self.feed[..m]);
        self.conv[SUR_L].process(&self.feed[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc_scaled(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
            surround_lin,
        );
        self.delay_sur_r
            .process(&self.stems[amb_r..amb_r + m], &mut self.mono[..m]);
        self.dec_sur_r.process(&self.mono[..m], &mut self.feed[..m]);
        self.conv[SUR_R].process(&self.feed[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc_scaled(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
            surround_lin,
        );

        let height_lin = self.mapped.height_lin;
        let direct_lin = self.mapped.height_direct_lin;
        let center_lin = self.mapped.center_height_lin;
        for i in 0..m {
            self.mono[i] = self.lateral[i] * direct_lin + self.stems[center_row + i] * center_lin;
        }
        self.delay_lift
            .process(&self.mono[..m], &mut self.lifted[..m]);

        self.dec_height[0].process(&self.stems[amb_l..amb_l + m], &mut self.feed[..m]);
        for i in 0..m {
            self.mono[i] = height_lin * (self.feed[i] + self.lifted[i]);
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
            self.mono[i] = height_lin * (self.feed[i] + self.lifted[i]);
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
            self.feed[i] *= height_lin;
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
            self.feed[i] *= height_lin;
        }
        self.conv[H0 + 3].process(&self.feed[..m], &mut self.tmp_l[..m], &mut self.tmp_r[..m]);
        acc(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );

        let send = self.mapped.direct_room_send_lin;
        let dir_l = 0;
        let dir_r = stride;
        for i in 0..m {
            let direct = 0.5 * (self.stems[dir_l + i] + self.stems[dir_r + i]);
            let amb = 0.5 * (self.stems[amb_l + i] + self.stems[amb_r + i]);
            self.room[i] = direct * send + amb;
        }
        let early_lin = self.mapped.early_lin;
        let n_early = self
            .early
            .len()
            .min(self.delay_early.len())
            .min(self.conv.len().saturating_sub(EARLY0));
        for t in 0..n_early {
            let gain = self.early[t].gain * early_lin;
            self.delay_early[t].process(&self.room[..m], &mut self.mono[..m]);
            for i in 0..m {
                self.mono[i] *= gain;
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
        let late_lin = self.mapped.late_lin;
        acc_scaled(
            &mut self.mix_l[..m],
            &mut self.mix_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
            late_lin,
        );

        for i in 0..m {
            self.mono[i] = 0.5 * (self.low_l[i] + self.low_r[i]);
        }
        self.delay_low.process(&self.mono[..m], &mut self.feed[..m]);
        for i in 0..m {
            self.mix_l[i] += self.feed[i];
            self.mix_r[i] += self.feed[i];
        }

        if !self.fir.coeff.is_empty() {
            self.fir.process(
                &self.mix_l[..m],
                &self.mix_r[..m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            self.mix_l[..m].copy_from_slice(&self.tmp_l[..m]);
            self.mix_r[..m].copy_from_slice(&self.tmp_r[..m]);
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
    }
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

fn acc_scaled(dst_l: &mut [f64], dst_r: &mut [f64], src_l: &[f64], src_r: &[f64], gain: f64) {
    for i in 0..dst_l.len() {
        dst_l[i] += gain * src_l[i];
        dst_r[i] += gain * src_r[i];
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
        let mapped = map_route_v(1.0, table.get("V2").unwrap(), "sadie", &table.delays_ms);
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
        let mapped = map_route_v(1.0, table.get("V2").unwrap(), "sadie", &table.delays_ms);
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
}
