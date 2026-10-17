//! Variable host blocks against a fixed block of 256.
//!
//! `numpy.random.default_rng(3)` with sizes in `1..=4096` used to desync the
//! upmix input FIFO at block 43. These tests keep that sequence, and sweep
//! 200 seeds at two `max_block` values on both routes.

use crate::hrir::{sha256_file, MAGIC, VERSION};
use crate::processor::{Processor, RenderConfig};
use crate::rng::NumpyGenerator;
use crate::RouteB;
use crate::RouteV;
use std::io::Write;
use std::path::PathBuf;

const SEEDS: u64 = 200;
/// Each random sequence runs at least this many samples, so it crosses several
/// STFT frames and can place a short block in front of a block near `max_block`.
const MIN_SAMPLES: usize = 8_192;
const SEED3_BLOCKS: usize = 200;

fn write_pack(dir: &std::path::Path, preset_rel: &str) -> (PathBuf, PathBuf, PathBuf) {
    let _ = std::fs::create_dir_all(dir);
    let source = dir.join("source.bin");
    let pack_path = dir.join("tiny.ahrp");
    let preset = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(preset_rel);
    std::fs::write(&source, b"synthetic-sofa").unwrap();
    let hash = sha256_file(&source).unwrap();
    let mut bytes = Vec::new();
    bytes.extend_from_slice(MAGIC);
    bytes.extend_from_slice(&VERSION.to_le_bytes());
    bytes.extend_from_slice(&48_000u32.to_le_bytes());
    bytes.extend_from_slice(&1u32.to_le_bytes());
    let taps = 8u32;
    bytes.extend_from_slice(&taps.to_le_bytes());
    let eq = b"frontal+power";
    bytes.extend_from_slice(&(eq.len() as u32).to_le_bytes());
    bytes.extend_from_slice(eq);
    bytes.extend_from_slice(&hash);
    for v in [0.0f64, 0.0, 0.0, 0.0] {
        bytes.extend_from_slice(&v.to_le_bytes());
    }
    for rep in 0..4 {
        for t in 0..taps {
            let s = if rep >= 2 && t == 0 { 1.0f32 } else { 0.0 };
            bytes.extend_from_slice(&s.to_le_bytes());
        }
    }
    let mut file = std::fs::File::create(&pack_path).unwrap();
    file.write_all(&bytes).unwrap();
    (pack_path, source, preset)
}

fn signal(n: usize) -> (Vec<f32>, Vec<f32>) {
    let mut left = vec![0.0; n];
    let mut right = vec![0.0; n];
    for i in 0..n {
        let t = i as f32;
        left[i] = 0.2 * (t * 0.07).sin() + 0.05 * (t * 0.013).sin();
        right[i] = 0.15 * (t * 0.05 + 0.4).sin() + 0.04 * (t * 0.019).cos();
    }
    (left, right)
}

fn numpy_blocks(seed: u64, max_block: usize, count: usize) -> Vec<usize> {
    let mut rng = NumpyGenerator::new(seed);
    let high = (max_block as i64) + 1;
    (0..count).map(|_| rng.integers(1, high) as usize).collect()
}

fn numpy_blocks_covering(seed: u64, max_block: usize, min_samples: usize) -> Vec<usize> {
    let mut rng = NumpyGenerator::new(seed);
    let high = (max_block as i64) + 1;
    let mut blocks = Vec::new();
    let mut n = 0usize;
    while n < min_samples {
        let block = rng.integers(1, high) as usize;
        blocks.push(block);
        n += block;
    }
    blocks
}

fn fixed_256(n: usize) -> Vec<usize> {
    let mut blocks = Vec::with_capacity(n.div_ceil(256));
    let mut left = n;
    while left > 0 {
        let m = 256.min(left);
        blocks.push(m);
        left -= m;
    }
    blocks
}

fn render(dsp: &mut dyn Processor, left: &[f32], right: &[f32], blocks: &[usize]) -> Vec<f32> {
    let n = left.len();
    let mut out_l = vec![0.0f32; n];
    let mut out_r = vec![0.0f32; n];
    let mut offset = 0;
    for &block in blocks {
        if offset >= n {
            break;
        }
        let m = block.min(n - offset);
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
    let mut stereo = Vec::with_capacity(n * 2);
    for i in 0..n {
        stereo.push(out_l[i]);
        stereo.push(out_r[i]);
    }
    stereo
}

fn worst_db(a: &[f32], b: &[f32]) -> (f64, usize) {
    let mut worst = -1000.0_f64;
    let mut at = 0usize;
    for (i, (u, v)) in a.iter().zip(b.iter()).enumerate() {
        let db = 20.0 * ((*u - *v).abs() as f64).max(1e-20).log10();
        if db > worst {
            worst = db;
            at = i;
        }
    }
    (worst, at)
}

fn prepare_route(route: &str, max_block: usize, dir_name: &str) -> Box<dyn Processor> {
    let dir = std::env::temp_dir().join(dir_name);
    if route == "B" {
        let (pack, source, preset) = write_pack(&dir, "../../presets/p0_draft.toml");
        let mut cfg = RenderConfig::route_b(&pack, &source, &preset);
        cfg.strength = 0.4;
        let mut dsp = RouteB::new();
        dsp.prepare(48_000, max_block, &cfg).unwrap();
        Box::new(dsp)
    } else {
        let (pack, source, preset) = write_pack(&dir, "../../presets/route_v.toml");
        let mut cfg = RenderConfig::route_v(&pack, &source, &preset);
        cfg.strength = 1.0;
        cfg.preset = "V2".to_string();
        let mut dsp = RouteV::new();
        dsp.prepare(48_000, max_block, &cfg).unwrap();
        Box::new(dsp)
    }
}

fn assert_blocks(
    dsp: &mut dyn Processor,
    route: &str,
    max_block: usize,
    seed: u64,
    blocks: &[usize],
) {
    let n: usize = blocks.iter().sum();
    let (left, right) = signal(n);
    dsp.reset();
    let reference = render(dsp, &left, &right, &fixed_256(n));
    dsp.reset();
    let got = render(dsp, &left, &right, blocks);
    let (db, at) = worst_db(&reference, &got);
    assert!(
        db <= -100.0,
        "{route} seed {seed} max_block {max_block} sample {at} {db} dBFS"
    );
}

fn sweep(route: &str, max_block: usize) {
    let mut dsp = prepare_route(
        route,
        max_block,
        &format!("aurora_fuzz_{route}_{max_block}"),
    );
    for seed in 0..SEEDS {
        let blocks = numpy_blocks_covering(seed, max_block, MIN_SAMPLES);
        assert_blocks(dsp.as_mut(), route, max_block, seed, &blocks);
    }
}

#[test]
fn route_b_seed3_matches_block_256() {
    let mut dsp = prepare_route("B", 4096, "aurora_fuzz_b_seed3");
    let blocks = numpy_blocks(3, 4096, SEED3_BLOCKS);
    assert_eq!(&blocks[42..45], &[316, 3988, 3867]);
    assert_blocks(dsp.as_mut(), "B", 4096, 3, &blocks);
}

#[test]
fn route_v_seed3_matches_block_256() {
    let mut dsp = prepare_route("V", 4096, "aurora_fuzz_v_seed3");
    let blocks = numpy_blocks(3, 4096, SEED3_BLOCKS);
    assert_eq!(&blocks[42..45], &[316, 3988, 3867]);
    assert_blocks(dsp.as_mut(), "V", 4096, 3, &blocks);
}

#[test]
fn route_b_random_blocks_max_512() {
    sweep("B", 512);
}

#[test]
fn route_b_random_blocks_max_4096() {
    sweep("B", 4096);
}

#[test]
fn route_v_random_blocks_max_512() {
    sweep("V", 512);
}

#[test]
fn route_v_random_blocks_max_4096() {
    sweep("V", 4096);
}
