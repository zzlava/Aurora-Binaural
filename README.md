# Aurora Binaural

**English** | [中文](README.zh-CN.md)

Aurora Binaural turns ordinary stereo music into **binaural spatial audio for headphones**. You get a wider sound field and a sense of height above you, while vocals stay close. It ships with a web GUI, a CLI and an HTTP API. The core is a real-time-safe Rust engine, designed so a real-time processing mode can be added later.

## Features

- **Spatial engine (route V)**:
  - Virtual speakers: front ±45°, sides ±90°, rear ±130°, plus four height speakers at 45° elevation.
  - Room: shoebox room model with ceiling and floor reflections, and an FDN reverb.
  - Vocals and the center image stay close to the listener.
  - Default HRTF: SADIE II D1 (Apache-2.0).
- **Three presets** (V3 is the default):
  - V1: open, vocals slightly farther
  - V2: immersive, vocals close
  - V3: immersive with overhead surround (recommended)
  - A classic "room" mode (route B) is also kept.
- **Five spatial controls**: strength, width, height, room and voice distance.
  - Adjustable from the GUI's Advanced panel, the CLI and the API.
  - You can change them during playback without clicks.
  - Save combinations as "My presets", with JSON import and export.
- **Hi-res input and output**:
  - Formats: FLAC, WAV (including RF64), ALAC, AIFF, AAC and MP3.
  - Input from 44.1 to 192 kHz. Exports default to the source sample rate.
  - Tags and cover art are preserved. 16-bit output gets TPDF dither.
  - Processing runs internally at 48 kHz float32 (ADR 0006).
- **Loudness-matched A/B listening**: versions are aligned with BS.1770, and switching between them keeps the playback position.
- **Low-frequency fidelity**:
  - Headroom is reserved automatically before processing, so the limiter no longer flattens the bass of loud masters (P1c).
  - Bass that is uncorrelated between channels, typical of spaced-microphone hall recordings, is no longer cancelled by a mono sum (P1d).
  - A coloration around 700 Hz has been fixed.

## Quick start (macOS)

```bash
cd ~/Desktop/leo/"Aurora Binaural"
bash scripts/setup.sh      # checks Rust, uv, Node and an ffmpeg with soxr (brew install ffmpeg-full is recommended), then builds the core and the web UI
uv run aurora gui          # opens the web GUI
```

CLI example: `uv run aurora convert input.flac --preset V3 --format flac --sample-rate source`. User docs are currently in Chinese: [quickstart](docs/user/quickstart.md), [CLI](docs/user/cli.md), [API](docs/user/api.md), [troubleshooting](docs/user/troubleshooting.md) and [deployment](docs/user/deploy.md).

## Status (2026-10-21)

| Phase | Scope | Status |
|---|---|---|
| P0 | Algorithm route and listening calibration (route B → calibration anchors → route V; the user chose V3) | ✅ accepted (`p0-accepted`) |
| P1a | Rust core: real-time safe, zero allocation, variable-block fuzzing, −1 dBTP true-peak limiter | ✅ |
| P1b | Five macro controls (default values reproduce the original presets bit for bit) | ✅ |
| P1c | Input headroom and bass balance (limiting on loud masters dropped from about 9 dB to at most 1.6 dB) | ✅ |
| P1d | Energy-preserving uncorrelated bass and a 700 Hz fix (Carmen bass deviation reduced from −5.2 dB to −1.1 dB) | ✅ |
| P3–P6 | App layer: CLI, FastAPI, SQLite worker, web GUI, Docker, hi-res formats, setup.sh, Advanced panel and My presets | ✅ |
| P6-05 | Export loudness: default "smart volume" (a fixed gain that brings the true peak to −1 dBTP, with no limiting), plus match-source, −14 LUFS and off; merge the duplicate strength slider | ⏳ specified, not yet dispatched |

## Documentation

These documents are written in Chinese.

- Plan: [docs/plan.md](docs/plan.md)
- Architecture decisions: [docs/adr/](docs/adr/)
- Task specs: [docs/tasks/](docs/tasks/)
- Acceptance records: `qa/p0` … `qa/p6`
- Agent workflow and dispatcher: [docs/agents.md](docs/agents.md)

## How it is built

Claude handles planning, task coordination, independent acceptance and Git. The code is written by Cursor (DSP core) and Codex (application layer). Tasks reach them through the `tools/dispatch.sh` dispatcher, which runs in a macOS terminal.

| Path | Owner |
|---|---|
| `core/` `prototypes/` `presets/` | Cursor |
| `src/` `tests/app/` `web/` `deploy/` `scripts/` | Codex |
| `qa/` `docs/` and Git operations | Claude |
| `data/` | Cursor adds data; Claude verifies source, license and hash |

Branch rules:

- Only work accepted by Claude is merged into `main`.
- Agent work happens on `agent/<agent>/<task>` branches.
- Each accepted phase is tagged `pN-accepted`.

## License

- The code is released under the [MIT License](LICENSE).
- Third-party data keeps its original license:
  - SADIE II HRTF: Apache-2.0.
  - TH Köln KU100 HRIR: CC BY-SA 3.0. It is used for local research only; see [data/hrtf/SOURCES.md](data/hrtf/SOURCES.md).
  - Test audio: CC0 or CC BY 3.0. Authors and sources are listed in [data/assets/registry.json](data/assets/registry.json).
- Full notices: [deploy/THIRD_PARTY_NOTICES.md](deploy/THIRD_PARTY_NOTICES.md).

## Notes

- The local folder name contains a space (`Aurora Binaural`), so always quote paths in scripts and commands.
- Audio under `qa/listen/` and in the repository root is the user's private, copyrighted listening material. It is git-ignored and must never be committed.
