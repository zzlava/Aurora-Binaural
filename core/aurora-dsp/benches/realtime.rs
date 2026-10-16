//! Single-core route B cost at 48 kHz, stereo, block size 256.
//!
//! Realtime factor is `(256 / 48000) / mean_seconds`. The acceptance target
//! is at least 20×.

use aurora_dsp::hrir::{sha256_file, MAGIC, VERSION};
use aurora_dsp::{Processor, RenderConfig, RouteB};
use criterion::{black_box, criterion_group, criterion_main, Criterion};
use std::time::Duration;

fn write_pack(dir: &std::path::Path) -> (std::path::PathBuf, std::path::PathBuf) {
    let source = dir.join("source.bin");
    let pack_path = dir.join("tiny.ahrp");
    std::fs::write(&source, b"bench-sofa").unwrap();
    let hash = sha256_file(&source).unwrap();
    let mut b = Vec::new();
    b.extend_from_slice(MAGIC);
    b.extend_from_slice(&VERSION.to_le_bytes());
    b.extend_from_slice(&48_000u32.to_le_bytes());
    b.extend_from_slice(&1u32.to_le_bytes());
    let taps = 256u32;
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
            let s = if rep >= 2 && t == 10 { 0.8f32 } else { 0.0 };
            b.extend_from_slice(&s.to_le_bytes());
        }
    }
    std::fs::write(&pack_path, b).unwrap();
    (pack_path, source)
}

fn route_b(c: &mut Criterion) {
    let dir = std::env::temp_dir().join("aurora_bench_hrir");
    let _ = std::fs::create_dir_all(&dir);
    let (pack, source) = write_pack(&dir);
    let preset =
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../presets/p0_draft.toml");
    let cfg = RenderConfig::route_b(pack, source, preset);
    let mut dsp = RouteB::new();
    dsp.prepare(48_000, 256, &cfg).unwrap();
    let n = 256;
    let left = vec![0.2f32; n];
    let right = vec![0.1f32; n];
    let mut out_l = vec![0.0f32; n];
    let mut out_r = vec![0.0f32; n];
    // Warm the filters so the measured loop is the steady state.
    for _ in 0..8 {
        dsp.process(&[&left, &right], &mut [&mut out_l, &mut out_r], n);
    }
    let mut group = c.benchmark_group("route_b");
    group.sample_size(20);
    group.measurement_time(Duration::from_secs(4));
    group.bench_function("block_256", |b| {
        b.iter(|| {
            dsp.process(
                black_box(&[&left, &right]),
                black_box(&mut [&mut out_l, &mut out_r]),
                n,
            );
        });
    });
    group.finish();
}

criterion_group!(benches, route_b);
criterion_main!(benches);
