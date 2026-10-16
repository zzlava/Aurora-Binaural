//! Uniform partitioned FFT convolution, and a non-uniform head/tail schedule.
//!
//! The partition size is fixed when the convolver is built. Host block size
//! only changes how samples are sliced into that partition, so 32 / 64 / 256 /
//! 1024 / 4096 and a single full buffer produce the same stream. The stream is
//! delayed by one partition; [`UniformConvolver::latency`] reports it.
//! Scratch and spectra are allocated in [`UniformConvolver::new`].

use num_complex::Complex;
use realfft::{ComplexToReal, RealFftPlanner, RealToComplex};
use std::sync::Arc;

fn copy_partition(time: &mut [f64], ir: &[f64], start: usize, part: usize) {
    time.fill(0.0);
    let n = ir.len().saturating_sub(start).min(part);
    if n > 0 {
        time[..n].copy_from_slice(&ir[start..start + n]);
    }
}

fn zero() -> Complex<f64> {
    Complex::new(0.0, 0.0)
}

fn finite(x: f64) -> f64 {
    if x.is_finite() {
        x
    } else {
        0.0
    }
}

/// Stereo uniform partitioned overlap-add.
///
/// Left and right share one forward FFT of the mono input. A second IR can be
/// crossfaded across `fade_samples` without allocating.
pub struct UniformConvolver {
    part: usize,
    fft_len: usize,
    n_parts: usize,
    forward: Arc<dyn RealToComplex<f64>>,
    inverse: Arc<dyn ComplexToReal<f64>>,
    scratch_fwd: Vec<Complex<f64>>,
    scratch_inv: Vec<Complex<f64>>,
    time_fft: Vec<f64>,
    acc: Vec<Complex<f64>>,
    ir_l: Vec<Vec<Complex<f64>>>,
    ir_r: Vec<Vec<Complex<f64>>>,
    ir_prev_l: Vec<Vec<Complex<f64>>>,
    ir_prev_r: Vec<Vec<Complex<f64>>>,
    in_spec: Vec<Vec<Complex<f64>>>,
    head: usize,
    stage: Vec<f64>,
    fill: usize,
    overlap_l: Vec<f64>,
    overlap_r: Vec<f64>,
    overlap_prev_l: Vec<f64>,
    overlap_prev_r: Vec<f64>,
    block_l: Vec<f64>,
    block_r: Vec<f64>,
    block_prev_l: Vec<f64>,
    block_prev_r: Vec<f64>,
    fifo_l: Vec<f64>,
    fifo_r: Vec<f64>,
    fifo_read: usize,
    fifo_count: usize,
    fade_pos: usize,
    fade_len: usize,
    has_ir: bool,
    ir_pad_l: Vec<f64>,
    ir_pad_r: Vec<f64>,
}

impl UniformConvolver {
    pub fn new(partition: usize, max_ir: usize, fade_samples: usize) -> Self {
        let part = partition.max(1);
        let fft_len = part * 2;
        let n_parts = max_ir.max(1).div_ceil(part);
        let mut planner = RealFftPlanner::<f64>::new();
        let forward = planner.plan_fft_forward(fft_len);
        let inverse = planner.plan_fft_inverse(fft_len);
        let scratch_fwd = forward.make_scratch_vec();
        let scratch_inv = inverse.make_scratch_vec();
        let spec_len = fft_len / 2 + 1;
        let blank = || vec![zero(); spec_len];
        Self {
            part,
            fft_len,
            n_parts,
            forward,
            inverse,
            scratch_fwd,
            scratch_inv,
            time_fft: vec![0.0; fft_len],
            acc: blank(),
            ir_l: (0..n_parts).map(|_| blank()).collect(),
            ir_r: (0..n_parts).map(|_| blank()).collect(),
            ir_prev_l: (0..n_parts).map(|_| blank()).collect(),
            ir_prev_r: (0..n_parts).map(|_| blank()).collect(),
            in_spec: (0..n_parts).map(|_| blank()).collect(),
            head: 0,
            stage: vec![0.0; part],
            fill: 0,
            overlap_l: vec![0.0; part],
            overlap_r: vec![0.0; part],
            overlap_prev_l: vec![0.0; part],
            overlap_prev_r: vec![0.0; part],
            block_l: vec![0.0; part],
            block_r: vec![0.0; part],
            block_prev_l: vec![0.0; part],
            block_prev_r: vec![0.0; part],
            fifo_l: vec![0.0; part * 2 + 2],
            fifo_r: vec![0.0; part * 2 + 2],
            fifo_read: 0,
            fifo_count: part,
            fade_pos: fade_samples.max(1),
            fade_len: fade_samples.max(1),
            has_ir: false,
            ir_pad_l: vec![0.0; n_parts * part],
            ir_pad_r: vec![0.0; n_parts * part],
        }
    }

    pub fn latency(&self) -> usize {
        self.part
    }

    pub fn set_ir(&mut self, ir_l: &[f64], ir_r: &[f64]) {
        if self.has_ir {
            for p in 0..self.n_parts {
                self.ir_prev_l[p].copy_from_slice(&self.ir_l[p]);
                self.ir_prev_r[p].copy_from_slice(&self.ir_r[p]);
            }
            self.overlap_prev_l.copy_from_slice(&self.overlap_l);
            self.overlap_prev_r.copy_from_slice(&self.overlap_r);
            self.fade_pos = 0;
        } else {
            self.fade_pos = self.fade_len;
        }
        self.ir_pad_l.fill(0.0);
        self.ir_pad_r.fill(0.0);
        let n = ir_l.len().min(self.ir_pad_l.len());
        self.ir_pad_l[..n].copy_from_slice(&ir_l[..n]);
        let nr = ir_r.len().min(self.ir_pad_r.len());
        self.ir_pad_r[..nr].copy_from_slice(&ir_r[..nr]);
        for p in 0..self.n_parts {
            self.fft_partition(p, true);
            self.fft_partition(p, false);
        }
        self.has_ir = true;
    }

    fn fft_partition(&mut self, part: usize, left: bool) {
        let start = part * self.part;
        if left {
            copy_partition(&mut self.time_fft, &self.ir_pad_l, start, self.part);
            let _ = self.forward.process_with_scratch(
                &mut self.time_fft,
                &mut self.ir_l[part],
                &mut self.scratch_fwd,
            );
        } else {
            copy_partition(&mut self.time_fft, &self.ir_pad_r, start, self.part);
            let _ = self.forward.process_with_scratch(
                &mut self.time_fft,
                &mut self.ir_r[part],
                &mut self.scratch_fwd,
            );
        }
    }

    pub fn process(&mut self, input: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = input.len().min(out_l.len()).min(out_r.len());
        for i in 0..n {
            self.stage[self.fill] = finite(input[i]);
            self.fill += 1;
            if self.fill == self.part {
                self.convolve_partition();
                self.fill = 0;
            }
            let (l, r) = self.pop();
            out_l[i] = l;
            out_r[i] = r;
        }
    }

    fn convolve_partition(&mut self) {
        self.time_fft.fill(0.0);
        self.time_fft[..self.part].copy_from_slice(&self.stage);
        let _ = self.forward.process_with_scratch(
            &mut self.time_fft,
            &mut self.acc,
            &mut self.scratch_fwd,
        );
        self.in_spec[self.head].copy_from_slice(&self.acc);
        self.mac(false, false);
        self.mac(false, true);
        if self.fade_pos < self.fade_len {
            self.mac(true, false);
            self.mac(true, true);
        }
        self.enqueue();
        self.head = (self.head + 1) % self.n_parts;
    }

    fn mac(&mut self, prev: bool, right: bool) {
        for bin in &mut self.acc {
            *bin = zero();
        }
        let n_bins = self.acc.len();
        for p in 0..self.n_parts {
            let idx = (self.head + self.n_parts - p) % self.n_parts;
            let ir = match (prev, right) {
                (false, false) => &self.ir_l[p],
                (false, true) => &self.ir_r[p],
                (true, false) => &self.ir_prev_l[p],
                (true, true) => &self.ir_prev_r[p],
            };
            let input = &self.in_spec[idx];
            for bin in 0..n_bins {
                self.acc[bin] += input[bin] * ir[bin];
            }
        }
        let _ = self.inverse.process_with_scratch(
            &mut self.acc,
            &mut self.time_fft,
            &mut self.scratch_inv,
        );
        let scale = 1.0 / self.fft_len as f64;
        let part = self.part;
        let (overlap, block) = match (prev, right) {
            (false, false) => (&mut self.overlap_l, &mut self.block_l),
            (false, true) => (&mut self.overlap_r, &mut self.block_r),
            (true, false) => (&mut self.overlap_prev_l, &mut self.block_prev_l),
            (true, true) => (&mut self.overlap_prev_r, &mut self.block_prev_r),
        };
        for i in 0..part {
            block[i] = self.time_fft[i] * scale + overlap[i];
            overlap[i] = self.time_fft[part + i] * scale;
        }
    }

    fn enqueue(&mut self) {
        let fading = self.fade_pos < self.fade_len;
        let part = self.part;
        let fade_len = self.fade_len;
        let fade_pos = self.fade_pos;
        for i in 0..part {
            let (mut yl, mut yr) = (self.block_l[i], self.block_r[i]);
            if fading {
                let t = ((fade_pos + i) as f64 / fade_len as f64).clamp(0.0, 1.0);
                yl = (1.0 - t) * self.block_prev_l[i] + t * yl;
                yr = (1.0 - t) * self.block_prev_r[i] + t * yr;
            }
            self.push(yl, yr);
        }
        if fading {
            self.fade_pos = (self.fade_pos + part).min(self.fade_len);
        }
    }

    fn push(&mut self, left: f64, right: f64) {
        let cap = self.fifo_l.len();
        let write = (self.fifo_read + self.fifo_count) % cap;
        self.fifo_l[write] = left;
        self.fifo_r[write] = right;
        self.fifo_count += 1;
    }

    fn pop(&mut self) -> (f64, f64) {
        if self.fifo_count == 0 {
            return (0.0, 0.0);
        }
        let l = self.fifo_l[self.fifo_read];
        let r = self.fifo_r[self.fifo_read];
        self.fifo_read = (self.fifo_read + 1) % self.fifo_l.len();
        self.fifo_count -= 1;
        (l, r)
    }

    pub fn reset(&mut self) {
        for spec in &mut self.in_spec {
            for bin in spec {
                *bin = zero();
            }
        }
        self.head = 0;
        self.stage.fill(0.0);
        self.fill = 0;
        self.overlap_l.fill(0.0);
        self.overlap_r.fill(0.0);
        self.overlap_prev_l.fill(0.0);
        self.overlap_prev_r.fill(0.0);
        self.block_prev_l.fill(0.0);
        self.block_prev_r.fill(0.0);
        self.fifo_l.fill(0.0);
        self.fifo_r.fill(0.0);
        self.fifo_read = 0;
        self.fifo_count = self.part;
        self.fade_pos = self.fade_len;
    }
}

/// Direct-form stereo FIR. Sample-aligned reference and a fallback for tests.
pub struct DirectStereoFir {
    n_taps: usize,
    ir_l: Vec<f64>,
    ir_r: Vec<f64>,
    hist: Vec<f64>,
    pos: usize,
}

impl DirectStereoFir {
    pub fn new(n_taps: usize) -> Self {
        let n = n_taps.max(1);
        Self {
            n_taps: n,
            ir_l: vec![0.0; n],
            ir_r: vec![0.0; n],
            hist: vec![0.0; n],
            pos: 0,
        }
    }

    pub fn set_ir(&mut self, ir_l: &[f64], ir_r: &[f64]) {
        self.ir_l.fill(0.0);
        self.ir_r.fill(0.0);
        let n = ir_l.len().min(self.n_taps);
        self.ir_l[..n].copy_from_slice(&ir_l[..n]);
        let nr = ir_r.len().min(self.n_taps);
        self.ir_r[..nr].copy_from_slice(&ir_r[..nr]);
    }

    pub fn process(&mut self, input: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = input.len().min(out_l.len()).min(out_r.len());
        let taps = self.n_taps;
        for i in 0..n {
            self.hist[self.pos] = finite(input[i]);
            let mut acc_l = 0.0;
            let mut acc_r = 0.0;
            let mut h = self.pos;
            for k in 0..taps {
                let s = self.hist[h];
                acc_l += s * self.ir_l[k];
                acc_r += s * self.ir_r[k];
                h = if h == 0 { taps - 1 } else { h - 1 };
            }
            out_l[i] = acc_l;
            out_r[i] = acc_r;
            self.pos += 1;
            if self.pos == taps {
                self.pos = 0;
            }
        }
    }

    pub fn reset(&mut self) {
        self.hist.fill(0.0);
        self.pos = 0;
    }
}

/// Long-IR schedule: a short uniform head plus a coarser tail partition.
///
/// Both segments are delayed to one common latency so their sum matches a
/// direct convolution of the concatenated IR.
pub struct NonUniformConvolver {
    head: UniformConvolver,
    tail: UniformConvolver,
    head_extra: usize,
    tail_extra: usize,
    head_delay_l: Vec<f64>,
    head_delay_r: Vec<f64>,
    tail_delay_l: Vec<f64>,
    tail_delay_r: Vec<f64>,
    head_pos: usize,
    tail_pos: usize,
    scratch_hl: Vec<f64>,
    scratch_hr: Vec<f64>,
    scratch_tl: Vec<f64>,
    scratch_tr: Vec<f64>,
    latency: usize,
    head_len: usize,
}

impl NonUniformConvolver {
    pub fn new(
        head_part: usize,
        head_len: usize,
        tail_part: usize,
        tail_len: usize,
        max_block: usize,
    ) -> Self {
        let head = UniformConvolver::new(head_part, head_len.max(head_part), 1);
        let tail = UniformConvolver::new(tail_part, tail_len.max(tail_part), 1);
        let head_at = head_part;
        // Align to the later of the two paths. The tail IR also starts
        // `head_len` samples into the full impulse, so its output needs that
        // shift on top of the partition delay.
        let tail_at = tail_part + head_len;
        let latency = head_at.max(tail_at);
        let head_extra = latency - head_at;
        let tail_extra = latency + head_len - tail_part;
        Self {
            head,
            tail,
            head_extra,
            tail_extra,
            head_delay_l: vec![0.0; head_extra.max(1)],
            head_delay_r: vec![0.0; head_extra.max(1)],
            tail_delay_l: vec![0.0; tail_extra.max(1)],
            tail_delay_r: vec![0.0; tail_extra.max(1)],
            head_pos: 0,
            tail_pos: 0,
            scratch_hl: vec![0.0; max_block.max(1)],
            scratch_hr: vec![0.0; max_block.max(1)],
            scratch_tl: vec![0.0; max_block.max(1)],
            scratch_tr: vec![0.0; max_block.max(1)],
            latency,
            head_len,
        }
    }

    pub fn latency(&self) -> usize {
        self.latency
    }

    pub fn set_ir(&mut self, ir_l: &[f64], ir_r: &[f64]) {
        let head_n = self.head_len.min(ir_l.len()).min(ir_r.len());
        self.head.set_ir(&ir_l[..head_n], &ir_r[..head_n]);
        let tail_l = if ir_l.len() > head_n {
            &ir_l[head_n..]
        } else {
            &[][..]
        };
        let tail_r = if ir_r.len() > head_n {
            &ir_r[head_n..]
        } else {
            &[][..]
        };
        self.tail.set_ir(tail_l, tail_r);
    }

    pub fn process(&mut self, input: &[f64], out_l: &mut [f64], out_r: &mut [f64]) {
        let n = input
            .len()
            .min(out_l.len())
            .min(out_r.len())
            .min(self.scratch_hl.len());
        self.head.process(
            &input[..n],
            &mut self.scratch_hl[..n],
            &mut self.scratch_hr[..n],
        );
        self.tail.process(
            &input[..n],
            &mut self.scratch_tl[..n],
            &mut self.scratch_tr[..n],
        );
        for i in 0..n {
            let (hl, hr) = delay_stereo(
                &mut self.head_delay_l,
                &mut self.head_delay_r,
                &mut self.head_pos,
                self.head_extra,
                self.scratch_hl[i],
                self.scratch_hr[i],
            );
            let (tl, tr) = delay_stereo(
                &mut self.tail_delay_l,
                &mut self.tail_delay_r,
                &mut self.tail_pos,
                self.tail_extra,
                self.scratch_tl[i],
                self.scratch_tr[i],
            );
            out_l[i] = hl + tl;
            out_r[i] = hr + tr;
        }
    }
}

fn delay_stereo(
    left: &mut [f64],
    right: &mut [f64],
    pos: &mut usize,
    delay: usize,
    xl: f64,
    xr: f64,
) -> (f64, f64) {
    if delay == 0 {
        return (xl, xr);
    }
    let yl = left[*pos];
    let yr = right[*pos];
    left[*pos] = xl;
    right[*pos] = xr;
    *pos += 1;
    if *pos == delay {
        *pos = 0;
    }
    (yl, yr)
}

/// Integer delay line. `delay == 0` copies.
pub struct DelayLine {
    buf: Vec<f64>,
    pos: usize,
    delay: usize,
}

impl DelayLine {
    pub fn new(delay: usize) -> Self {
        Self {
            buf: vec![0.0; delay.max(1)],
            pos: 0,
            delay,
        }
    }

    pub fn process(&mut self, input: &[f64], output: &mut [f64]) {
        let n = input.len().min(output.len());
        if self.delay == 0 {
            output[..n].copy_from_slice(&input[..n]);
            return;
        }
        for i in 0..n {
            output[i] = self.buf[self.pos];
            self.buf[self.pos] = finite(input[i]);
            self.pos += 1;
            if self.pos == self.delay {
                self.pos = 0;
            }
        }
    }

    pub fn reset(&mut self) {
        self.buf.fill(0.0);
        self.pos = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_blocks(conv: &mut UniformConvolver, x: &[f64], block: usize) -> (Vec<f64>, Vec<f64>) {
        let mut yl = vec![0.0; x.len()];
        let mut yr = vec![0.0; x.len()];
        let mut o = 0;
        while o < x.len() {
            let n = block.min(x.len() - o);
            conv.process(&x[o..o + n], &mut yl[o..o + n], &mut yr[o..o + n]);
            o += n;
        }
        (yl, yr)
    }

    #[test]
    fn uniform_matches_direct_after_its_latency() {
        let ir_l: Vec<f64> = (0..40).map(|i| 0.05 * ((i as f64) * 0.7).sin()).collect();
        let ir_r: Vec<f64> = (0..40).map(|i| 0.04 * ((i as f64) * 0.3).cos()).collect();
        let x: Vec<f64> = (0..1200).map(|i| ((i as f64) * 0.17).sin()).collect();
        let mut conv = UniformConvolver::new(32, 64, 16);
        conv.set_ir(&ir_l, &ir_r);
        let (yl, yr) = run_blocks(&mut conv, &x, 10);
        let mut fir = DirectStereoFir::new(64);
        fir.set_ir(&ir_l, &ir_r);
        let mut ref_l = vec![0.0; x.len()];
        let mut ref_r = vec![0.0; x.len()];
        fir.process(&x, &mut ref_l, &mut ref_r);
        let delay = conv.latency();
        let mut worst = 0.0_f64;
        for i in delay..x.len() {
            worst = worst.max((yl[i] - ref_l[i - delay]).abs());
            worst = worst.max((yr[i] - ref_r[i - delay]).abs());
        }
        assert!(worst < 1e-8, "worst {worst}");
    }

    #[test]
    fn uniform_block_sizes_agree_below_minus_100_dbfs() {
        let ir: Vec<f64> = (0..80).map(|i| 0.01 * ((i % 7) as f64 - 3.0)).collect();
        let x: Vec<f64> = (0..2048)
            .map(|i| ((i * 17) % 100) as f64 / 50.0 - 1.0)
            .collect();
        let render = |block: usize| {
            let mut conv = UniformConvolver::new(64, 128, 8);
            conv.set_ir(&ir, &ir);
            run_blocks(&mut conv, &x, block).0
        };
        let a = render(32);
        let b = render(256);
        let c = render(x.len());
        for (u, v) in a.iter().zip(b.iter()) {
            let db = 20.0 * (u - v).abs().max(1e-20).log10();
            assert!(db <= -100.0, "{db}");
        }
        for (u, v) in b.iter().zip(c.iter()) {
            let db = 20.0 * (u - v).abs().max(1e-20).log10();
            assert!(db <= -100.0, "{db}");
        }
    }

    #[test]
    fn crossfade_moves_between_two_gains() {
        let mut conv = UniformConvolver::new(32, 32, 64);
        conv.set_ir(&[1.0], &[1.0]);
        let ones = vec![1.0; 256];
        let mut yl = vec![0.0; ones.len()];
        let mut yr = vec![0.0; ones.len()];
        conv.process(&ones, &mut yl, &mut yr);
        // Steady DC gain of an impulse IR is 1, after the partition delay.
        assert!((yl[200] - 1.0).abs() < 1e-6, "{}", yl[200]);
        conv.set_ir(&[0.5], &[0.5]);
        let mut yl2 = vec![0.0; 256];
        let mut yr2 = vec![0.0; 256];
        conv.process(&ones, &mut yl2, &mut yr2);
        // After the fade (64 samples) plus one partition of delay, gain is 0.5.
        assert!((yl2[200] - 0.5).abs() < 1e-3, "settled {}", yl2[200]);
        let _ = yr2;
    }

    #[test]
    fn nonuniform_matches_direct_after_shared_latency() {
        let ir_l: Vec<f64> = (0..180).map(|i| 0.02 * ((i as f64) * 0.31).sin()).collect();
        let ir_r: Vec<f64> = (0..180)
            .map(|i| 0.015 * ((i as f64) * 0.17).cos())
            .collect();
        let x: Vec<f64> = (0..1600).map(|i| ((i as f64) * 0.11).sin()).collect();
        let mut conv = NonUniformConvolver::new(32, 64, 128, 256, x.len());
        conv.set_ir(&ir_l, &ir_r);
        let mut yl = vec![0.0; x.len()];
        let mut yr = vec![0.0; x.len()];
        conv.process(&x, &mut yl, &mut yr);
        let mut fir = DirectStereoFir::new(256);
        fir.set_ir(&ir_l, &ir_r);
        let mut ref_l = vec![0.0; x.len()];
        let mut ref_r = vec![0.0; x.len()];
        fir.process(&x, &mut ref_l, &mut ref_r);
        let delay = conv.latency();
        let mut worst = 0.0_f64;
        for i in delay..x.len() {
            worst = worst.max((yl[i] - ref_l[i - delay]).abs());
            worst = worst.max((yr[i] - ref_r[i - delay]).abs());
        }
        assert!(worst < 1e-6, "nonuniform worst {worst} latency {delay}");
    }
}
