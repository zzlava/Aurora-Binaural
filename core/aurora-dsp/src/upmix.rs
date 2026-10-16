//! Streaming STFT direct/ambient upmix (prototype `StreamingUpmixer`, method D1).
//!
//! The hop is fixed, so host blocks of any size produce the same samples.
//! Output is delayed by `n_fft`. [`StreamingUpmix::latency`] reports that delay.

use num_complex::Complex;
use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};
use std::sync::Arc;

pub const N_FFT: usize = 1024;
pub const HOP: usize = 256;
pub const N_SECTORS: usize = 5;
pub const N_STEMS: usize = 6 + N_SECTORS;
pub const WIDE_DEG: f64 = 90.0;
pub const REAR_DEG: f64 = 130.0;

pub struct StreamingUpmix {
    n_fft: usize,
    hop: usize,
    latency: usize,
    aw: Vec<f64>,
    hann: Vec<f64>,
    alpha: f64,
    pub width_deg: f64,
    pub ambient_mix: f64,
    pub sector_az: [f64; N_SECTORS],
    gll: Vec<f64>,
    grr: Vec<f64>,
    glr_re: Vec<f64>,
    glr_im: Vec<f64>,
    prev_mdir: Vec<f64>,
    has_prev: bool,
    in_l: Vec<f64>,
    in_r: Vec<f64>,
    in_len: usize,
    in_origin: i64,
    consumed: i64,
    frame: i64,
    acc: Vec<f64>,
    w: Vec<f64>,
    acc_cap: usize,
    acc_end: usize,
    acc_origin: i64,
    read: i64,
    forward: Arc<dyn RealToComplex<f64>>,
    inverse: Arc<dyn ComplexToReal<f64>>,
    scratch_fwd: Vec<Complex<f64>>,
    scratch_inv: Vec<Complex<f64>>,
    time: Vec<f64>,
    spec_l: Vec<Complex<f64>>,
    spec_r: Vec<Complex<f64>>,
    masked: Vec<Complex<f64>>,
    pow_l: Vec<f64>,
    pow_r: Vec<f64>,
    cross_re: Vec<f64>,
    cross_im: Vec<f64>,
    phi: Vec<f64>,
    pan: Vec<f64>,
    m_dir: Vec<f64>,
    m_wide: Vec<f64>,
    m_amb: Vec<f64>,
    weights: Vec<f64>,
    frame_buf: Vec<f64>,
    pub coherence_sum: f64,
    pub coherence_n: u64,
}

impl StreamingUpmix {
    pub fn new(sample_rate: f64, max_block: usize, width_deg: f64, ambient_mix: f64) -> Self {
        let n_fft = N_FFT;
        let hop = HOP;
        let mut hann = vec![0.0; n_fft];
        let mut aw = vec![0.0; n_fft];
        for k in 0..n_fft {
            hann[k] = 0.5 - 0.5 * (2.0 * std::f64::consts::PI * k as f64 / n_fft as f64).cos();
            aw[k] = hann[k].sqrt();
        }
        let alpha = 1.0 - (-(hop as f64) / (0.040 * sample_rate)).exp();
        let mut planner = RealFftPlanner::<f64>::new();
        let forward = planner.plan_fft_forward(n_fft);
        let inverse = planner.plan_fft_inverse(n_fft);
        let bins = n_fft / 2 + 1;
        let acc_cap = 4 * n_fft + max_block.max(n_fft);
        let in_cap = n_fft + max_block.max(n_fft);
        let mut up = Self {
            n_fft,
            hop,
            latency: n_fft,
            aw,
            hann,
            alpha,
            width_deg: 0.0,
            ambient_mix: 0.0,
            sector_az: [0.0; N_SECTORS],
            gll: vec![0.0; bins],
            grr: vec![0.0; bins],
            glr_re: vec![0.0; bins],
            glr_im: vec![0.0; bins],
            prev_mdir: vec![0.0; bins],
            has_prev: false,
            in_l: vec![0.0; in_cap],
            in_r: vec![0.0; in_cap],
            in_len: 0,
            in_origin: 0,
            consumed: 0,
            frame: 0,
            acc: vec![0.0; N_STEMS * acc_cap],
            w: vec![0.0; acc_cap],
            acc_cap,
            acc_end: 0,
            acc_origin: 0,
            read: 0,
            scratch_fwd: forward.make_scratch_vec(),
            scratch_inv: inverse.make_scratch_vec(),
            forward,
            inverse,
            time: vec![0.0; n_fft],
            spec_l: vec![Complex::new(0.0, 0.0); bins],
            spec_r: vec![Complex::new(0.0, 0.0); bins],
            masked: vec![Complex::new(0.0, 0.0); bins],
            pow_l: vec![0.0; bins],
            pow_r: vec![0.0; bins],
            cross_re: vec![0.0; bins],
            cross_im: vec![0.0; bins],
            phi: vec![0.0; bins],
            pan: vec![0.0; bins],
            m_dir: vec![0.0; bins],
            m_wide: vec![0.0; bins],
            m_amb: vec![0.0; bins],
            weights: vec![0.0; bins * N_SECTORS],
            frame_buf: vec![0.0; n_fft],
            coherence_sum: 0.0,
            coherence_n: 0,
        };
        up.set_width_ambient(width_deg, ambient_mix);
        up
    }

    pub fn latency(&self) -> usize {
        self.latency
    }

    pub fn set_width_ambient(&mut self, width_deg: f64, ambient_mix: f64) {
        self.width_deg = width_deg.max(0.0);
        self.ambient_mix = ambient_mix.clamp(0.0, 1.0);
        if self.width_deg < 5.0 {
            self.sector_az = [0.0; N_SECTORS];
        } else {
            for i in 0..N_SECTORS {
                self.sector_az[i] =
                    -self.width_deg + (2.0 * self.width_deg) * i as f64 / (N_SECTORS - 1) as f64;
            }
        }
    }

    pub fn reset(&mut self) {
        self.gll.fill(0.0);
        self.grr.fill(0.0);
        self.glr_re.fill(0.0);
        self.glr_im.fill(0.0);
        self.prev_mdir.fill(0.0);
        self.has_prev = false;
        self.in_len = 0;
        self.in_origin = 0;
        self.consumed = 0;
        self.frame = 0;
        self.acc.fill(0.0);
        self.w.fill(0.0);
        self.acc_end = 0;
        self.acc_origin = 0;
        self.read = 0;
        self.coherence_sum = 0.0;
        self.coherence_n = 0;
    }

    /// `out` is stem-major with `stride` samples per stem. Sample `i` of this
    /// block is written at `out[stem * stride + offset + i]`.
    pub fn process(
        &mut self,
        left: &[f64],
        right: &[f64],
        out: &mut [f64],
        stride: usize,
        offset: usize,
    ) {
        let n = left.len().min(right.len());
        for s in 0..N_STEMS {
            let row = s * stride + offset;
            for i in 0..n {
                out[row + i] = 0.0;
            }
        }
        self.append(&left[..n], &right[..n]);
        self.analyze();
        self.emit(n, out, stride, offset);
        self.compact_input();
        self.compact_acc();
    }

    fn append(&mut self, left: &[f64], right: &[f64]) {
        let n = left.len();
        if self.in_len + n > self.in_l.len() {
            self.compact_input();
        }
        let room = self.in_l.len() - self.in_len;
        let n = n.min(room);
        let end = self.in_len + n;
        self.in_l[self.in_len..end].copy_from_slice(&left[..n]);
        self.in_r[self.in_len..end].copy_from_slice(&right[..n]);
        self.in_len = end;
        self.consumed += n as i64;
    }

    fn analyze(&mut self) {
        let n_fft = self.n_fft;
        let hop = self.hop as i64;
        while self.consumed >= self.frame * hop + n_fft as i64 {
            let start = self.frame * hop;
            let local = (start - self.in_origin) as usize;
            self.window_fft(local, true);
            self.window_fft(local, false);
            self.coherence_and_pan();
            self.masks();
            self.synth_masked(0, true, Mask::Direct);
            self.synth_masked(1, false, Mask::Direct);
            self.synth_masked(2, true, Mask::Wide);
            self.synth_masked(3, false, Mask::Wide);
            self.synth_masked(4, true, Mask::Amb);
            self.synth_masked(5, false, Mask::Amb);
            self.sector_weights();
            for s in 0..N_SECTORS {
                self.synth_sector(6 + s, s);
                self.overlap(start, 6 + s);
            }
            let mut mean = 0.0;
            for p in &self.phi {
                mean += *p;
            }
            self.coherence_sum += mean / self.phi.len() as f64;
            self.coherence_n += 1;
            self.frame += 1;
        }
    }

    fn window_fft(&mut self, local: usize, left: bool) {
        let n_fft = self.n_fft;
        if left {
            for i in 0..n_fft {
                self.time[i] = self.in_l[local + i] * self.aw[i];
            }
            let _ = self.forward.process_with_scratch(
                &mut self.time,
                &mut self.spec_l,
                &mut self.scratch_fwd,
            );
        } else {
            for i in 0..n_fft {
                self.time[i] = self.in_r[local + i] * self.aw[i];
            }
            let _ = self.forward.process_with_scratch(
                &mut self.time,
                &mut self.spec_r,
                &mut self.scratch_fwd,
            );
        }
    }

    fn coherence_and_pan(&mut self) {
        let bins = self.phi.len();
        for i in 0..bins {
            let l = self.spec_l[i];
            let r = self.spec_r[i];
            self.pow_l[i] = l.norm_sqr();
            self.pow_r[i] = r.norm_sqr();
            let c = l * r.conj();
            self.cross_re[i] = c.re;
            self.cross_im[i] = c.im;
        }
        smooth_bins(&mut self.pow_l, &mut self.frame_buf);
        smooth_bins(&mut self.pow_r, &mut self.frame_buf);
        smooth_bins(&mut self.cross_re, &mut self.frame_buf);
        smooth_bins(&mut self.cross_im, &mut self.frame_buf);
        let a = self.alpha;
        for i in 0..bins {
            self.gll[i] = (1.0 - a) * self.gll[i] + a * self.pow_l[i];
            self.grr[i] = (1.0 - a) * self.grr[i] + a * self.pow_r[i];
            self.glr_re[i] = (1.0 - a) * self.glr_re[i] + a * self.cross_re[i];
            self.glr_im[i] = (1.0 - a) * self.glr_im[i] + a * self.cross_im[i];
            let den = (self.gll[i] * self.grr[i]).max(1e-20).sqrt();
            let mag = (self.glr_re[i] * self.glr_re[i] + self.glr_im[i] * self.glr_im[i]).sqrt();
            self.phi[i] = (mag / den).clamp(0.0, 1.0);
            let g = (self.gll[i] - self.grr[i]) / (self.gll[i] + self.grr[i]).max(1e-20);
            self.pan[i] = g.clamp(-1.0, 1.0);
        }
    }

    fn masks(&mut self) {
        let n = self.m_dir.len();
        for i in 0..n {
            let raw = ((self.phi[i] - 0.22) / 0.78).clamp(0.0, 1.0);
            self.m_dir[i] = raw * raw;
        }
        if !self.has_prev {
            self.prev_mdir.copy_from_slice(&self.m_dir);
            self.has_prev = true;
        } else {
            for i in 0..n {
                let delta = (self.m_dir[i] - self.prev_mdir[i]).clamp(-0.35, 0.35);
                self.m_dir[i] = self.prev_mdir[i] + delta;
                self.prev_mdir[i] = self.m_dir[i];
            }
        }
        let amb = self.ambient_mix;
        for i in 0..n {
            let incoh = 1.0 - self.m_dir[i];
            self.m_amb[i] = incoh * amb;
            self.m_wide[i] = incoh * (1.0 - amb);
        }
    }

    fn synth_masked(&mut self, stem: usize, left: bool, kind: Mask) {
        let n = self.masked.len();
        for i in 0..n {
            let mask = match kind {
                Mask::Direct => self.m_dir[i],
                Mask::Wide => self.m_wide[i],
                Mask::Amb => self.m_amb[i],
            };
            let spec = if left { self.spec_l[i] } else { self.spec_r[i] };
            self.masked[i] = spec * mask;
        }
        self.inverse_to_frame();
        // start is recovered by the caller via overlap after we stash frame.
        // synth_masked overlaps immediately; the caller passes start through a field.
        let start = self.frame * self.hop as i64;
        self.overlap(start, stem);
    }

    fn synth_sector(&mut self, _stem: usize, sector: usize) {
        let bins = self.masked.len();
        for i in 0..bins {
            let l = self.spec_l[i] * self.m_dir[i];
            let r = self.spec_r[i] * self.m_dir[i];
            let power = l.norm_sqr() + r.norm_sqr();
            let mix = l + r;
            let mag = mix.norm();
            let phase = if mag > 1e-20 {
                mix / mag
            } else {
                Complex::new(1.0, 0.0)
            };
            let w = self.weights[i * N_SECTORS + sector];
            self.masked[i] = phase * power.sqrt() * w;
        }
        self.inverse_to_frame();
    }

    fn inverse_to_frame(&mut self) {
        let _ = self.inverse.process_with_scratch(
            &mut self.masked,
            &mut self.time,
            &mut self.scratch_inv,
        );
        let scale = 1.0 / self.n_fft as f64;
        for i in 0..self.n_fft {
            self.frame_buf[i] = self.time[i] * scale * self.aw[i];
        }
    }

    fn sector_weights(&mut self) {
        let bins = self.phi.len();
        let n_sec = N_SECTORS;
        self.weights.fill(0.0);
        if self.width_deg < 5.0 {
            for i in 0..bins {
                self.weights[i * n_sec] = 1.0;
            }
            return;
        }
        let span = self.sector_az[n_sec - 1] - self.sector_az[0];
        let step = span / (n_sec - 1) as f64;
        for i in 0..bins {
            let az = self.pan[i] * self.width_deg;
            let pos = (az - self.sector_az[0]) / step;
            let i0 = pos.floor().clamp(0.0, (n_sec - 2) as f64) as usize;
            let frac = (pos - i0 as f64).clamp(0.0, 1.0);
            let w0 = (frac * std::f64::consts::FRAC_PI_2).cos();
            let w1 = (frac * std::f64::consts::FRAC_PI_2).sin();
            self.weights[i * n_sec + i0] += w0;
            self.weights[i * n_sec + i0 + 1] += w1;
        }
    }

    fn overlap(&mut self, start: i64, stem: usize) {
        let end = start + self.n_fft as i64;
        self.ensure_acc(end);
        let local = (start - self.acc_origin) as usize;
        let row = stem * self.acc_cap;
        for i in 0..self.n_fft {
            self.acc[row + local + i] += self.frame_buf[i];
        }
        if stem == 0 {
            for i in 0..self.n_fft {
                self.w[local + i] += self.hann[i];
            }
        }
        let end_local = local + self.n_fft;
        if end_local > self.acc_end {
            self.acc_end = end_local;
        }
    }

    fn ensure_acc(&mut self, abs_end: i64) {
        let have = self.acc_origin + self.acc_cap as i64;
        if abs_end <= have {
            return;
        }
        self.compact_acc();
    }

    fn emit(&mut self, n: usize, out: &mut [f64], stride: usize, offset: usize) {
        let latency = self.latency as i64;
        for i in 0..n {
            let j = self.read + i as i64 - latency;
            if j < 0 {
                continue;
            }
            let local = j - self.acc_origin;
            if local < 0 || local as usize >= self.w.len() {
                continue;
            }
            let w = self.w[local as usize];
            if w > 1e-12 {
                for s in 0..N_STEMS {
                    out[s * stride + offset + i] = self.acc[s * self.acc_cap + local as usize] / w;
                }
            }
        }
        self.read += n as i64;
    }

    fn compact_input(&mut self) {
        let keep = self.frame * self.hop as i64;
        let drop = keep - self.in_origin;
        if drop > self.hop as i64 && (drop as usize) < self.in_len {
            let d = drop as usize;
            self.in_l.copy_within(d..self.in_len, 0);
            self.in_r.copy_within(d..self.in_len, 0);
            self.in_len -= d;
            self.in_origin = keep;
        }
    }

    fn compact_acc(&mut self) {
        let keep = (self.read - self.latency as i64 - self.n_fft as i64).max(0);
        let drop = keep - self.acc_origin;
        if drop > self.n_fft as i64
            && (drop as usize) < self.acc_end
            && (drop as usize) < self.acc_cap
        {
            let d = drop as usize;
            let end = self.acc_end.min(self.acc_cap);
            if d >= end {
                return;
            }
            for s in 0..N_STEMS {
                let row = s * self.acc_cap;
                self.acc.copy_within(row + d..row + end, row);
                for i in (end - d)..self.acc_cap {
                    self.acc[row + i] = 0.0;
                }
            }
            self.w.copy_within(d..end, 0);
            for i in (end - d)..self.acc_cap {
                self.w[i] = 0.0;
            }
            self.acc_origin = keep;
            self.acc_end = end - d;
        }
    }
}

enum Mask {
    Direct,
    Wide,
    Amb,
}

/// Numpy `convolve(..., mode="same")` with a length-7 moving average.
/// `scratch` must be at least `input.len()` and is only used as temporary storage.
fn smooth_bins(input: &mut [f64], scratch: &mut [f64]) {
    let n = input.len();
    let radius = 3usize;
    let klen = 2 * radius + 1;
    let scale = 1.0 / klen as f64;
    for i in 0..n {
        let mut acc = 0.0;
        for k in 0..klen {
            let j = i as isize + k as isize - radius as isize;
            if j >= 0 && (j as usize) < n {
                acc += input[j as usize];
            }
        }
        scratch[i] = acc * scale;
    }
    input[..n].copy_from_slice(&scratch[..n]);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine_pair(n: usize) -> (Vec<f64>, Vec<f64>) {
        let mut l = vec![0.0; n];
        let mut r = vec![0.0; n];
        for i in 0..n {
            let t = i as f64;
            l[i] = 0.2 * (2.0 * std::f64::consts::PI * 440.0 * t / 48000.0).sin()
                + 0.1 * (2.0 * std::f64::consts::PI * 1000.0 * t / 48000.0).sin();
            r[i] = 0.2 * (2.0 * std::f64::consts::PI * 440.0 * t / 48000.0 + 0.4).sin()
                + 0.05 * (2.0 * std::f64::consts::PI * 2000.0 * t / 48000.0).sin();
        }
        (l, r)
    }

    fn render(block: usize) -> Vec<f64> {
        let n = 4096;
        let (left, right) = sine_pair(n);
        let mut up = StreamingUpmix::new(48_000.0, block.max(n), 60.8, 0.4);
        let mut stems = vec![0.0; N_STEMS * n];
        let mut o = 0;
        while o < n {
            let m = block.min(n - o);
            up.process(&left[o..o + m], &right[o..o + m], &mut stems, n, o);
            o += m;
        }
        stems
    }

    #[test]
    fn matches_prototype_reference() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../qa/p1/cursor/reference/upmix_stems.f64");
        let bytes = std::fs::read(&path).expect("upmix reference");
        let (chunks, _) = bytes.as_chunks::<8>();
        let expect: Vec<f64> = chunks
            .iter()
            .map(|chunk| f64::from_le_bytes(*chunk))
            .collect();
        let got = render(256);
        assert_eq!(got.len(), expect.len());
        let mut worst = 0.0_f64;
        for (g, e) in got.iter().zip(expect.iter()) {
            worst = worst.max((g - e).abs());
        }
        assert!(worst < 1e-5, "upmix worst abs {worst}");
    }

    #[test]
    fn block_sizes_agree() {
        let a = render(32);
        let b = render(256);
        let c = render(4096);
        for (u, v) in a.iter().zip(b.iter()) {
            let db = 20.0 * (u - v).abs().max(1e-20).log10();
            assert!(db <= -100.0, "{db}");
        }
        for (u, v) in b.iter().zip(c.iter()) {
            let db = 20.0 * (u - v).abs().max(1e-20).log10();
            assert!(db <= -100.0, "{db}");
        }
    }
}
