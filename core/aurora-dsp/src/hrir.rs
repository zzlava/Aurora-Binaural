//! Aurora HRIR pack: directions, minimum-phase HRIRs, ITD, render HRIRs.
//!
//! Loading hashes the source SOFA file and compares it to the hash stored in
//! the pack. A mismatch is an error. There is no fallback dataset.

use crate::error::DspError;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::Read;
use std::path::Path;

pub const MAGIC: &[u8; 4] = b"AHRP";
pub const VERSION: u32 = 1;

#[derive(Debug, Clone)]
pub struct Direction {
    pub azimuth_deg: f64,
    pub elevation_deg: f64,
    pub itd_left: f64,
    pub itd_right: f64,
    pub minphase_l: Vec<f32>,
    pub minphase_r: Vec<f32>,
    pub render_l: Vec<f32>,
    pub render_r: Vec<f32>,
}

#[derive(Debug, Clone)]
pub struct HrirPack {
    pub sample_rate: u32,
    pub eq_mode: String,
    pub source_sha256: [u8; 32],
    pub dirs: Vec<Direction>,
}

impl HrirPack {
    pub fn n_taps(&self) -> usize {
        self.dirs.first().map(|d| d.render_l.len()).unwrap_or(0)
    }

    pub fn load(pack_path: &Path, source_path: &Path) -> Result<Self, DspError> {
        let bytes = std::fs::read(pack_path).map_err(|source| DspError::Io {
            path: pack_path.to_path_buf(),
            source,
        })?;
        let pack = parse_pack(&bytes).map_err(|reason| DspError::Pack {
            path: pack_path.to_path_buf(),
            reason,
        })?;
        let actual = sha256_file(source_path)?;
        if actual != pack.source_sha256 {
            return Err(DspError::HashMismatch {
                file: source_path.to_path_buf(),
                expected: hex(&pack.source_sha256),
                actual: hex(&actual),
            });
        }
        Ok(pack)
    }
}

pub fn sha256_file(path: &Path) -> Result<[u8; 32], DspError> {
    let mut file = File::open(path).map_err(|source| DspError::Io {
        path: path.to_path_buf(),
        source,
    })?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 64 * 1024];
    loop {
        let n = file.read(&mut buf).map_err(|source| DspError::Io {
            path: path.to_path_buf(),
            source,
        })?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    let digest = hasher.finalize();
    let mut out = [0u8; 32];
    out.copy_from_slice(&digest);
    Ok(out)
}

pub fn hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0xf) as usize] as char);
    }
    s
}

fn parse_pack(bytes: &[u8]) -> Result<HrirPack, String> {
    let mut c = Cursor::new(bytes);
    let magic = c.bytes(4)?;
    if magic != MAGIC {
        return Err(format!("bad magic {magic:?}"));
    }
    let version = c.u32()?;
    if version != VERSION {
        return Err(format!("unsupported pack version {version}"));
    }
    let sample_rate = c.u32()?;
    let n_dirs = c.u32()? as usize;
    let n_taps = c.u32()? as usize;
    let eq_len = c.u32()? as usize;
    let eq_mode = String::from_utf8(c.bytes(eq_len)?.to_vec())
        .map_err(|_| "eq mode is not utf-8".to_string())?;
    let mut source_sha256 = [0u8; 32];
    source_sha256.copy_from_slice(c.bytes(32)?);
    let mut dirs = Vec::with_capacity(n_dirs);
    for _ in 0..n_dirs {
        dirs.push(Direction {
            azimuth_deg: c.f64()?,
            elevation_deg: c.f64()?,
            itd_left: c.f64()?,
            itd_right: c.f64()?,
            minphase_l: c.f32s(n_taps)?,
            minphase_r: c.f32s(n_taps)?,
            render_l: c.f32s(n_taps)?,
            render_r: c.f32s(n_taps)?,
        });
    }
    if c.rest() != 0 {
        return Err(format!("{} trailing bytes", c.rest()));
    }
    Ok(HrirPack {
        sample_rate,
        eq_mode,
        source_sha256,
        dirs,
    })
}

struct Cursor<'a> {
    data: &'a [u8],
    at: usize,
}

impl<'a> Cursor<'a> {
    fn new(data: &'a [u8]) -> Self {
        Self { data, at: 0 }
    }
    fn rest(&self) -> usize {
        self.data.len().saturating_sub(self.at)
    }
    fn bytes(&mut self, n: usize) -> Result<&'a [u8], String> {
        if self.at + n > self.data.len() {
            return Err("truncated pack".into());
        }
        let s = &self.data[self.at..self.at + n];
        self.at += n;
        Ok(s)
    }
    fn u32(&mut self) -> Result<u32, String> {
        let b = self.bytes(4)?;
        Ok(u32::from_le_bytes(b.try_into().unwrap()))
    }
    fn f64(&mut self) -> Result<f64, String> {
        let b = self.bytes(8)?;
        Ok(f64::from_le_bytes(b.try_into().unwrap()))
    }
    fn f32s(&mut self, n: usize) -> Result<Vec<f32>, String> {
        let b = self.bytes(n * 4)?;
        let mut out = Vec::with_capacity(n);
        let (chunks, _) = b.as_chunks::<4>();
        for chunk in chunks {
            out.push(f32::from_le_bytes(*chunk));
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn tiny_pack(hash: [u8; 32]) -> Vec<u8> {
        let mut b = Vec::new();
        b.extend_from_slice(MAGIC);
        b.extend_from_slice(&VERSION.to_le_bytes());
        b.extend_from_slice(&48_000u32.to_le_bytes());
        b.extend_from_slice(&1u32.to_le_bytes());
        b.extend_from_slice(&2u32.to_le_bytes());
        let eq = b"frontal+power";
        b.extend_from_slice(&(eq.len() as u32).to_le_bytes());
        b.extend_from_slice(eq);
        b.extend_from_slice(&hash);
        b.extend_from_slice(&0.0f64.to_le_bytes());
        b.extend_from_slice(&0.0f64.to_le_bytes());
        b.extend_from_slice(&1.5f64.to_le_bytes());
        b.extend_from_slice(&2.5f64.to_le_bytes());
        for _ in 0..8 {
            b.extend_from_slice(&0.25f32.to_le_bytes());
        }
        b
    }

    #[test]
    fn hash_mismatch_refuses_to_load() {
        let dir = std::env::temp_dir().join("aurora_hrir_hash_test");
        let _ = std::fs::create_dir_all(&dir);
        let source = dir.join("src.bin");
        let pack_path = dir.join("p.ahrp");
        std::fs::write(&source, b"sofa-bytes").unwrap();
        let hash = sha256_file(&source).unwrap();
        let mut bad = hash;
        bad[0] ^= 0xff;
        let mut f = std::fs::File::create(&pack_path).unwrap();
        f.write_all(&tiny_pack(bad)).unwrap();
        let err = HrirPack::load(&pack_path, &source).unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("hash mismatch") || msg.contains("Hash"),
            "{msg}"
        );
        assert!(!msg.to_lowercase().contains("fallback"));
    }
}
