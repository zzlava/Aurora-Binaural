//! Route B block processor.
//!
//! Low frequencies stay mono through an LR4 crossover and take the same DRR
//! gain as the high image. The high band is a streaming direct/ambient upmix
//! (coherence floor, bleed, HF pan and HF direct fade from the preset), HRIR
//! convolved, with a fixed interaural delay on the side and bed paths, a
//! raised-cosine side fade, shoebox early reflections and a seeded FDN. A
//! 512-tap minimum-phase FIR from the preset's fixed EQ follows the mix. The
//! true-peak limiter is the last stage. `strength == 0` returns the stereo
//! input and reports zero latency. `process` does not allocate.

use crate::bed::BandLift;
use crate::conv::{DelayLine, UniformConvolver};
use crate::crossover::Lr4Crossover;
use crate::decorrelate::Decorrelator;
use crate::early::{EarlyReflections, N_DIRECTED};
use crate::error::DspError;
use crate::fdn::FdnReverb;
use crate::fixed_eq::{design_minphase_fir, StereoFir};
use crate::guard::TimbreGuard;
use crate::hrir::HrirPack;
use crate::interpolate::idw_render;
use crate::limiter::{TruePeakLimiter, LOOKAHEAD};
use crate::params::{
    canonical_hrtf_key, infer_hrtf_key, map_strength, MappedStrength, PresetTable,
};
use crate::processor::{Params, Processor, RenderConfig};
use crate::side_lpf::design_side_lpf;
use crate::upmix::{StreamingUpmix, UpmixControls, N_FFT, N_SECTORS, N_STEMS};

const PART: usize = 64;
const FADE: usize = 128;
const N_CONV: usize = N_SECTORS + 4 + N_DIRECTED;

pub struct RouteB {
    engine: Option<Engine>,
}

struct Engine {
    max_block: usize,
    bypass: bool,
    limiter_on: bool,
    timbre_on: bool,
    mapped: MappedStrength,
    seed: u64,
    preset_id: String,
    hrtf_key: String,
    delays_ms: Vec<f64>,
    content_frames: Option<usize>,
    rendered: usize,
    table: PresetTable,
    pack: HrirPack,
    hrir_peak: usize,
    xo: Lr4Crossover,
    up: StreamingUpmix,
    dec_wide_l: Decorrelator,
    dec_wide_r: Decorrelator,
    dec_amb_l: Decorrelator,
    dec_amb_r: Decorrelator,
    dec_side_l: Decorrelator,
    dec_side_r: Decorrelator,
    conv: Vec<UniformConvolver>,
    early: EarlyReflections,
    fdn: FdnReverb,
    delay_low: DelayLine,
    delay_side_l: DelayLine,
    delay_side_r: DelayLine,
    delay_fdn_l: DelayLine,
    delay_fdn_r: DelayLine,
    delay_room_l: DelayLine,
    delay_room_r: DelayLine,
    limiter: TruePeakLimiter,
    guard: TimbreGuard,
    eq: StereoFir,
    side_fir: StereoFir,
    side_fir_delay: usize,
    bed: BandLift,
    in_l: Vec<f64>,
    in_r: Vec<f64>,
    low_l: Vec<f64>,
    low_r: Vec<f64>,
    high_l: Vec<f64>,
    high_r: Vec<f64>,
    stems: Vec<f64>,
    wide_l: Vec<f64>,
    wide_r: Vec<f64>,
    amb_l: Vec<f64>,
    amb_r: Vec<f64>,
    bed_l: Vec<f64>,
    bed_r: Vec<f64>,
    side_in: Vec<f64>,
    side_l: Vec<f64>,
    side_r: Vec<f64>,
    side_dl: Vec<f64>,
    side_dr: Vec<f64>,
    mid: Vec<f64>,
    er_mono: Vec<f64>,
    early_dir: Vec<f64>,
    feed: Vec<f64>,
    fdn_l: Vec<f64>,
    fdn_r: Vec<f64>,
    img_l: Vec<f64>,
    img_r: Vec<f64>,
    room_l: Vec<f64>,
    room_r: Vec<f64>,
    room_dl: Vec<f64>,
    room_dr: Vec<f64>,
    low_d: Vec<f64>,
    mix_l: Vec<f64>,
    mix_r: Vec<f64>,
    tmp_l: Vec<f64>,
    tmp_r: Vec<f64>,
    ir_l: Vec<f64>,
    ir_r: Vec<f64>,
    silence: Vec<f64>,
}

impl RouteB {
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

impl Default for RouteB {
    fn default() -> Self {
        Self::new()
    }
}

impl Processor for RouteB {
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
        let table = PresetTable::load(&config.preset_path)?;
        let row = table.get(&config.preset)?.clone();
        let pack = HrirPack::load(&config.hrtf_pack, &config.hrtf_source)?;
        if pack.sample_rate != sample_rate {
            return Err(DspError::Message(format!(
                "HRIR pack sample rate is {}, refusing to fall back to another rate",
                pack.sample_rate
            )));
        }
        if !pack.eq_mode.contains(&row.eq_mode) {
            return Err(DspError::Message(format!(
                "HRIR pack eq mode '{}' does not match preset '{}', refusing to fall back",
                pack.eq_mode, row.eq_mode
            )));
        }
        let n_taps = pack.n_taps().max(1);
        let mapped = map_strength(config.strength, &row);
        let hrtf_key = if config.hrtf_name.is_empty() {
            infer_hrtf_key(&config.hrtf_pack, &config.hrtf_source).ok_or_else(|| {
                DspError::Preset {
                    path: config.preset_path.clone(),
                    reason: format!(
                        "preset '{}' fixed_eq needs an HRTF id; pass hrtf_name or a KU100/SADIE path",
                        config.preset
                    ),
                }
            })?
        } else {
            canonical_hrtf_key(&config.hrtf_name)
        };
        let (bands, gains) = row.eq_curve(&hrtf_key, config.strength.max(0.2))?;
        let eq = StereoFir::new(&design_minphase_fir(&bands, &gains, f64::from(sample_rate)));
        let (side_fir, side_fir_delay) = match design_side_lpf(f64::from(sample_rate), mapped.side_lpf_hz)
        {
            Some((coeff, delay)) => (StereoFir::new(&coeff), delay),
            None => (StereoFir::bypass(), 0),
        };
        let delays_ms = table.delays_for(&row).to_vec();
        let mb = max_block;
        let mut conv = Vec::with_capacity(N_CONV);
        for _ in 0..N_CONV {
            conv.push(UniformConvolver::new(PART, n_taps, FADE));
        }
        let align = N_FFT + PART;
        let mut engine = Engine {
            max_block: mb,
            bypass: mapped.bypass,
            limiter_on: config.limiter,
            timbre_on: config.timbre_guard,
            mapped,
            seed: config.seed,
            preset_id: config.preset.clone(),
            hrtf_key,
            delays_ms: delays_ms.clone(),
            content_frames: config.content_frames,
            rendered: 0,
            table,
            pack,
            hrir_peak: 0,
            xo: Lr4Crossover::new(f64::from(sample_rate), row.crossover_hz),
            up: StreamingUpmix::with_controls(
                f64::from(sample_rate),
                mb,
                upmix_controls(&mapped),
            ),
            dec_wide_l: Decorrelator::new(sample_rate, config.seed + 3, &delays_ms),
            dec_wide_r: Decorrelator::new(sample_rate, config.seed + 4, &delays_ms),
            dec_amb_l: Decorrelator::new(sample_rate, config.seed + 5, &delays_ms),
            dec_amb_r: Decorrelator::new(sample_rate, config.seed + 6, &delays_ms),
            dec_side_l: Decorrelator::new(sample_rate, config.seed + 11, &delays_ms),
            dec_side_r: Decorrelator::new(sample_rate, config.seed + 12, &delays_ms),
            conv,
            early: EarlyReflections::new(sample_rate),
            fdn: FdnReverb::new(sample_rate, row.t60, config.seed),
            delay_low: DelayLine::new(align),
            delay_side_l: DelayLine::new(align),
            delay_side_r: DelayLine::new(align),
            delay_fdn_l: DelayLine::new(PART),
            delay_fdn_r: DelayLine::new(PART),
            delay_room_l: DelayLine::new(N_FFT),
            delay_room_r: DelayLine::new(N_FFT),
            limiter: TruePeakLimiter::new(sample_rate),
            guard: if config.timbre_guard {
                TimbreGuard::from_fir(&[1.0])
            } else {
                TimbreGuard::disabled()
            },
            eq,
            side_fir,
            side_fir_delay,
            bed: BandLift::new(
                f64::from(sample_rate),
                mapped.bed_peak_lo_hz,
                mapped.bed_peak_hi_hz,
                mapped.bed_peak_db,
            ),
            in_l: vec![0.0; mb],
            in_r: vec![0.0; mb],
            low_l: vec![0.0; mb],
            low_r: vec![0.0; mb],
            high_l: vec![0.0; mb],
            high_r: vec![0.0; mb],
            stems: vec![0.0; N_STEMS * mb],
            wide_l: vec![0.0; mb],
            wide_r: vec![0.0; mb],
            amb_l: vec![0.0; mb],
            amb_r: vec![0.0; mb],
            bed_l: vec![0.0; mb],
            bed_r: vec![0.0; mb],
            side_in: vec![0.0; mb],
            side_l: vec![0.0; mb],
            side_r: vec![0.0; mb],
            side_dl: vec![0.0; mb],
            side_dr: vec![0.0; mb],
            mid: vec![0.0; mb],
            er_mono: vec![0.0; mb],
            early_dir: vec![0.0; N_DIRECTED * mb],
            feed: vec![0.0; mb],
            fdn_l: vec![0.0; mb],
            fdn_r: vec![0.0; mb],
            img_l: vec![0.0; mb],
            img_r: vec![0.0; mb],
            room_l: vec![0.0; mb],
            room_r: vec![0.0; mb],
            room_dl: vec![0.0; mb],
            room_dr: vec![0.0; mb],
            low_d: vec![0.0; mb],
            mix_l: vec![0.0; mb],
            mix_r: vec![0.0; mb],
            tmp_l: vec![0.0; mb],
            tmp_r: vec![0.0; mb],
            ir_l: vec![0.0; n_taps],
            ir_r: vec![0.0; n_taps],
            silence: vec![0.0; mb],
        };
        engine.install_irs();
        engine.realign();
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
        let mapped = map_strength(params.strength, &row);
        let Ok((bands, gains)) = row.eq_curve(&engine.hrtf_key, params.strength.max(0.2)) else {
            return;
        };
        let width_changed = (mapped.width_deg - engine.mapped.width_deg).abs() > 1e-9;
        let bed_changed = (mapped.wide_deg - engine.mapped.wide_deg).abs() > 1e-9
            || (mapped.rear_deg - engine.mapped.rear_deg).abs() > 1e-9;
        let cutoff_changed = (mapped.crossover_hz - engine.mapped.crossover_hz).abs() > 1e-9;
        let t60_changed = (mapped.t60 - engine.mapped.t60).abs() > 1e-12;
        let seed_changed = params.seed != engine.seed;
        let lpf_changed = (mapped.side_lpf_hz - engine.mapped.side_lpf_hz).abs() > 1e-9;
        let bed_db_changed = (mapped.bed_peak_db - engine.mapped.bed_peak_db).abs() > 1e-9
            || (mapped.bed_peak_lo_hz - engine.mapped.bed_peak_lo_hz).abs() > 1e-6
            || (mapped.bed_peak_hi_hz - engine.mapped.bed_peak_hi_hz).abs() > 1e-6;
        let delays_ms = engine.table.delays_for(&row).to_vec();
        engine.mapped = mapped;
        engine.bypass = mapped.bypass;
        engine.preset_id = params.preset.clone();
        engine.delays_ms = delays_ms.clone();
        engine.up.set_controls(upmix_controls(&mapped));
        engine
            .eq
            .set_coeffs(&design_minphase_fir(&bands, &gains, 48_000.0));
        if cutoff_changed {
            engine.xo = Lr4Crossover::new(48_000.0, mapped.crossover_hz);
        }
        if seed_changed || t60_changed {
            engine.seed = params.seed;
            engine.fdn.reseed(48_000, mapped.t60, params.seed);
        }
        if seed_changed {
            engine.dec_wide_l = Decorrelator::new(48_000, params.seed + 3, &delays_ms);
            engine.dec_wide_r = Decorrelator::new(48_000, params.seed + 4, &delays_ms);
            engine.dec_amb_l = Decorrelator::new(48_000, params.seed + 5, &delays_ms);
            engine.dec_amb_r = Decorrelator::new(48_000, params.seed + 6, &delays_ms);
            engine.dec_side_l = Decorrelator::new(48_000, params.seed + 11, &delays_ms);
            engine.dec_side_r = Decorrelator::new(48_000, params.seed + 12, &delays_ms);
        }
        if lpf_changed {
            match design_side_lpf(48_000.0, mapped.side_lpf_hz) {
                Some((coeff, delay)) => {
                    engine.side_fir.set_coeffs(&coeff);
                    engine.side_fir_delay = delay;
                }
                None => {
                    engine.side_fir = StereoFir::bypass();
                    engine.side_fir_delay = 0;
                }
            }
            engine.realign();
        }
        if bed_db_changed {
            engine.bed = BandLift::new(
                48_000.0,
                mapped.bed_peak_lo_hz,
                mapped.bed_peak_hi_hz,
                mapped.bed_peak_db,
            );
        }
        if width_changed {
            engine.install_sector_irs();
        }
        if bed_changed {
            engine.install_bed_irs();
        }
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
        self.engine.as_ref().map(RouteB::latency_of).unwrap_or(0)
    }

    fn tail_samples(&self) -> usize {
        match &self.engine {
            Some(engine) if !engine.bypass => (0.45 * 48_000.0) as usize,
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
            engine.silence[..m].fill(0.0);
            // Reuse the input slots without allocating a second pair of slices.
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
        engine.dec_amb_l.reset();
        engine.dec_amb_r.reset();
        engine.dec_side_l.reset();
        engine.dec_side_r.reset();
        for conv in &mut engine.conv {
            conv.reset();
        }
        engine.early.reset();
        engine.fdn.reset();
        engine.delay_low.reset();
        engine.delay_side_l.reset();
        engine.delay_side_r.reset();
        engine.delay_fdn_l.reset();
        engine.delay_fdn_r.reset();
        engine.delay_room_l.reset();
        engine.delay_room_r.reset();
        engine.limiter.reset();
        engine.guard.reset();
        engine.eq.reset();
        engine.side_fir.reset();
        engine.bed.reset();
        engine.rendered = 0;
    }
}

impl Engine {
    fn install_irs(&mut self) {
        self.hrir_peak = self.interpolate_peak(0.0, 0.0);
        self.install_sector_irs();
        self.install_bed_irs();
        let taps: Vec<(f64, f64)> = self
            .early
            .taps()
            .iter()
            .take(N_DIRECTED)
            .map(|t| (t.azimuth_deg, t.elevation_deg))
            .collect();
        for (i, (az, el)) in taps.iter().enumerate() {
            self.set_conv(N_SECTORS + 4 + i, *az, *el);
        }
    }

    fn install_bed_irs(&mut self) {
        let wide = self.mapped.wide_deg;
        let rear = self.mapped.rear_deg;
        self.set_conv(N_SECTORS, wide, 0.0);
        self.set_conv(N_SECTORS + 1, -wide, 0.0);
        self.set_conv(N_SECTORS + 2, rear, 0.0);
        self.set_conv(N_SECTORS + 3, -rear, 0.0);
    }

    fn realign(&mut self) {
        let low_delay = N_FFT + self.hrir_peak + PART;
        let side_delay = low_delay.saturating_sub(self.side_fir_delay);
        self.delay_low = DelayLine::new(low_delay);
        self.delay_side_l = DelayLine::new(side_delay);
        self.delay_side_r = DelayLine::new(side_delay);
    }

    fn install_sector_irs(&mut self) {
        let az = self.up.sector_az;
        for (i, az) in az.iter().enumerate() {
            self.set_conv(i, *az, 0.0);
        }
    }

    fn interpolate_peak(&mut self, az: f64, el: f64) -> usize {
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
        idw_render(&self.pack, az, el, 3, &mut self.ir_l, &mut self.ir_r);
        let (l, r) = split_ir(&mut self.ir_l, &mut self.ir_r);
        self.conv[index].set_ir(l, r);
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
        self.img_l[..m].fill(0.0);
        self.img_r[..m].fill(0.0);
        for s in 0..N_SECTORS {
            let row = (6 + s) * stride;
            self.conv[s].process(
                &self.stems[row..row + m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            for i in 0..m {
                self.img_l[i] += self.tmp_l[i];
                self.img_r[i] += self.tmp_r[i];
            }
        }
        self.dec_wide_l.process(
            &self.stems[2 * stride..2 * stride + m],
            &mut self.wide_l[..m],
        );
        self.dec_wide_r.process(
            &self.stems[3 * stride..3 * stride + m],
            &mut self.wide_r[..m],
        );
        self.dec_amb_l.process(
            &self.stems[4 * stride..4 * stride + m],
            &mut self.amb_l[..m],
        );
        self.dec_amb_r.process(
            &self.stems[5 * stride..5 * stride + m],
            &mut self.amb_r[..m],
        );
        self.bed_l[..m].fill(0.0);
        self.bed_r[..m].fill(0.0);
        self.conv[N_SECTORS].process(
            &self.wide_l[..m],
            &mut self.tmp_l[..m],
            &mut self.tmp_r[..m],
        );
        acc(
            &mut self.bed_l[..m],
            &mut self.bed_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );
        self.conv[N_SECTORS + 1].process(
            &self.wide_r[..m],
            &mut self.tmp_l[..m],
            &mut self.tmp_r[..m],
        );
        acc(
            &mut self.bed_l[..m],
            &mut self.bed_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );
        self.conv[N_SECTORS + 2].process(
            &self.amb_l[..m],
            &mut self.tmp_l[..m],
            &mut self.tmp_r[..m],
        );
        acc(
            &mut self.bed_l[..m],
            &mut self.bed_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );
        self.conv[N_SECTORS + 3].process(
            &self.amb_r[..m],
            &mut self.tmp_l[..m],
            &mut self.tmp_r[..m],
        );
        acc(
            &mut self.bed_l[..m],
            &mut self.bed_r[..m],
            &self.tmp_l[..m],
            &self.tmp_r[..m],
        );
        self.bed.process(&mut self.bed_l[..m], &mut self.bed_r[..m]);
        for i in 0..m {
            self.img_l[i] += self.bed_l[i];
            self.img_r[i] += self.bed_r[i];
        }

        for i in 0..m {
            self.side_in[i] = 0.5 * (self.high_l[i] - self.high_r[i]);
        }
        self.dec_side_l
            .process(&self.side_in[..m], &mut self.side_l[..m]);
        self.dec_side_r
            .process(&self.side_in[..m], &mut self.side_r[..m]);
        self.side_fir.process(
            &self.side_l[..m],
            &self.side_r[..m],
            &mut self.tmp_l[..m],
            &mut self.tmp_r[..m],
        );
        self.delay_side_l
            .process(&self.tmp_l[..m], &mut self.side_dl[..m]);
        self.delay_side_r
            .process(&self.tmp_r[..m], &mut self.side_dr[..m]);

        for i in 0..m {
            self.mid[i] = 0.5 * (self.high_l[i] + self.high_r[i]);
        }
        self.early.process(
            &self.mid[..m],
            &mut self.er_mono[..m],
            &mut self.early_dir,
            stride,
        );
        self.room_l[..m].fill(0.0);
        self.room_r[..m].fill(0.0);
        let early_k = self.mapped.early_level;
        for t in 0..N_DIRECTED {
            let row = t * stride;
            self.conv[N_SECTORS + 4 + t].process(
                &self.early_dir[row..row + m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            for i in 0..m {
                self.room_l[i] += early_k * self.tmp_l[i];
                self.room_r[i] += early_k * self.tmp_r[i];
            }
        }
        for i in 0..m {
            self.feed[i] = self.mid[i] * 0.7 + self.er_mono[i] * 0.3;
        }
        self.fdn
            .process(&self.feed[..m], &mut self.fdn_l[..m], &mut self.fdn_r[..m]);
        self.delay_fdn_l
            .process(&self.fdn_l[..m], &mut self.tmp_l[..m]);
        self.delay_fdn_r
            .process(&self.fdn_r[..m], &mut self.tmp_r[..m]);
        let late_k = self.mapped.late_level;
        for i in 0..m {
            self.room_l[i] += late_k * self.tmp_l[i];
            self.room_r[i] += late_k * self.tmp_r[i];
        }
        self.delay_room_l
            .process(&self.room_l[..m], &mut self.room_dl[..m]);
        self.delay_room_r
            .process(&self.room_r[..m], &mut self.room_dr[..m]);
        for i in 0..m {
            self.low_d[i] = 0.5 * (self.low_l[i] + self.low_r[i]);
        }
        self.delay_low
            .process(&self.low_d[..m], &mut self.tmp_l[..m]);
        self.low_d[..m].copy_from_slice(&self.tmp_l[..m]);

        let drr = 10.0_f64.powf(self.mapped.drr_offset_db / 20.0);
        let side_g = self.mapped.side_decor_gain;
        for i in 0..m {
            let image_l = self.img_l[i] + side_g * self.side_dl[i];
            let image_r = self.img_r[i] + side_g * self.side_dr[i];
            // Bass takes the same DRR gain as the high image. Otherwise
            // strength reads as a bass shelf once the median level is removed.
            self.mix_l[i] = image_l * drr + self.room_dl[i] + self.low_d[i] * drr;
            self.mix_r[i] = image_r * drr + self.room_dr[i] + self.low_d[i] * drr;
        }
        self.crossfade_tail(m, drr);
        if !self.bypass {
            self.eq.process(
                &self.mix_l[..m],
                &self.mix_r[..m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            self.mix_l[..m].copy_from_slice(&self.tmp_l[..m]);
            self.mix_r[..m].copy_from_slice(&self.tmp_r[..m]);
        }
        if self.timbre_on {
            self.guard.process(
                &self.mix_l[..m],
                &self.mix_r[..m],
                &mut self.tmp_l[..m],
                &mut self.tmp_r[..m],
            );
            self.mix_l[..m].copy_from_slice(&self.tmp_l[..m]);
            self.mix_r[..m].copy_from_slice(&self.tmp_r[..m]);
        }
        // Limiter is the last stage. The fixed EQ and the optional timbre
        // guard both sit in front of it.
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
        self.rendered += m;
    }

    fn crossfade_tail(&mut self, m: usize, drr: f64) {
        let Some(total) = self.content_frames else {
            return;
        };
        let n_tail = 128.min(total / 8);
        if n_tail <= 8 || total < n_tail {
            return;
        }
        let start = total - n_tail;
        let denom = (n_tail - 1) as f64;
        for i in 0..m {
            let pos = self.rendered + i;
            if pos >= start && pos < total {
                let fade = (pos - start) as f64 / denom;
                let dry_l = self.in_l[i] * drr;
                let dry_r = self.in_r[i] * drr;
                self.mix_l[i] = self.mix_l[i] * (1.0 - fade) + dry_l * fade;
                self.mix_r[i] = self.mix_r[i] * (1.0 - fade) + dry_r * fade;
            }
        }
    }
}

fn upmix_controls(mapped: &MappedStrength) -> UpmixControls {
    UpmixControls {
        width_deg: mapped.width_deg,
        ambient_mix: mapped.ambient_ratio,
        coherence_floor: mapped.coherence_floor,
        coherence_power: mapped.coherence_power,
        pan_gamma: mapped.pan_gamma,
        direct_bleed: mapped.direct_bleed,
        hf_pan_floor: mapped.hf_pan_floor,
        hf_pan_start_hz: mapped.hf_pan_start_hz,
        hf_pan_end_hz: mapped.hf_pan_end_hz,
        hf_direct_start_hz: mapped.hf_direct_start_hz,
        hf_direct_end_hz: mapped.hf_direct_end_hz,
    }
}

fn acc(dst_l: &mut [f64], dst_r: &mut [f64], src_l: &[f64], src_r: &[f64]) {
    for i in 0..dst_l.len() {
        dst_l[i] += src_l[i];
        dst_r[i] += src_r[i];
    }
}

fn split_ir<'a>(left: &'a mut [f64], right: &'a mut [f64]) -> (&'a [f64], &'a [f64]) {
    (left, right)
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
    use std::io::Write;
    use std::path::PathBuf;

    fn write_pack(dir: &std::path::Path) -> (PathBuf, PathBuf, PathBuf) {
        let source = dir.join("source.bin");
        let pack_path = dir.join("tiny.ahrp");
        let preset = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../presets/p0_draft.toml");
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
        // minphase L/R then render L/R: an impulse on both ears.
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

    fn prepared(max_block: usize, strength: f64) -> RouteB {
        prepared_len(max_block, strength, None)
    }

    fn prepared_len(max_block: usize, strength: f64, frames: Option<usize>) -> RouteB {
        let dir = std::env::temp_dir().join(format!("aurora_chain_{strength}_{max_block}"));
        let _ = std::fs::create_dir_all(&dir);
        let (pack, source, preset) = write_pack(&dir);
        let mut cfg = RenderConfig::route_b(&pack, &source, &preset);
        cfg.strength = strength;
        cfg.hrtf_name = "ku100".to_string();
        cfg.content_frames = frames;
        let mut dsp = RouteB::new();
        dsp.prepare(48_000, max_block, &cfg).unwrap();
        dsp
    }

    fn render(dsp: &mut RouteB, left: &[f32], right: &[f32], block: usize) -> Vec<f32> {
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
    fn strength_zero_bypasses() {
        let mut dsp = prepared(256, 0.0);
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
            let mut dsp = prepared_len(n, 0.4, Some(n));
            render(&mut dsp, &left, &right, n)
        };
        for block in [32usize, 64, 256, 1024, 4096] {
            let mut dsp = prepared_len(n, 0.4, Some(n));
            let got = render(&mut dsp, &left, &right, block);
            for (u, v) in got.iter().zip(reference.iter()) {
                let db = 20.0 * ((*u - *v).abs() as f64).max(1e-20).log10();
                assert!(db <= -100.0, "block {block} {db}");
            }
        }
    }

    #[test]
    fn process_does_not_allocate_and_nan_is_finite() {
        let mut dsp = prepared(512, 0.4);
        let n = 256;
        let mut left = vec![0.1f32; n];
        left[3] = f32::NAN;
        left[8] = f32::INFINITY;
        let right = vec![0.0f32; n];
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
