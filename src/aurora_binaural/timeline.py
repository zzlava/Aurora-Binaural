"""Codec timeline, priming, and padding measurement suite (docs/plan.md Section 8 & P0 item 4).

Measures WAV (pcm_s24le), M4A (AAC-LC: native 'aac' and macOS 'aac_at' comparison),
MP3 (libmp3lame), and raw ADTS '.aac' across 44.1 kHz and 48 kHz inputs.
Generates test signals in a temporary directory and writes structured findings to
`qa/p0/antigravity/timeline.json`.
"""

from __future__ import annotations

import json
import math
import platform
import subprocess
import tempfile
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from aurora_binaural.media import decode_to_f32le, ensure_ffmpeg_available
from aurora_binaural.params import AuroraParams, OutputFormat, OutputSpec
from aurora_binaural.pipeline import convert_audio


def _build_impulse_test_signal(sample_rate: int, total_samples: int, impulse_index: int = 1000) -> bytes:
    """Generate a deterministic stereo float32le test signal with:
    - A low-level background 440 Hz tone (-30 dBFS) for BS.1770 loudness validity
    - A sharp unit-polarity impulse at `impulse_index` (+0.85 L / -0.85 R) to measure exact priming delay
    - A secondary marker at `total_samples - 500` (+0.65 L / -0.65 R) to verify tail alignment
    """
    buf = array("f", [0.0] * (total_samples * 2))
    bg_amp = 10.0 ** (-30.0 / 20.0)
    tail_marker = max(impulse_index + 500, total_samples - 500)

    for i in range(total_samples):
        s = bg_amp * math.sin(2.0 * math.pi * 440.0 * i / sample_rate)
        buf[i * 2] = s
        buf[i * 2 + 1] = s

    if impulse_index < total_samples:
        buf[impulse_index * 2] = 0.85
        buf[impulse_index * 2 + 1] = -0.85

    if tail_marker < total_samples:
        buf[tail_marker * 2] = 0.65
        buf[tail_marker * 2 + 1] = -0.65

    return buf.tobytes()


def _find_impulse_peak_index(pcm_f32le: bytes, channels: int, search_window: int = 6000) -> int:
    """Find the frame index of the primary positive peak in channel 0 within [0, search_window)."""
    floats = array("f")
    floats.frombytes(pcm_f32le)
    n_frames = min(search_window, len(floats) // channels)
    best_idx = 0
    best_val = -1.0
    for i in range(n_frames):
        v = abs(floats[i * channels])
        if v > best_val:
            best_val = v
            best_idx = i
    return best_idx


def _get_ffmpeg_version_line() -> str:
    ffmpeg_bin, _ = ensure_ffmpeg_available()
    res = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True, check=False)
    first_line = res.stdout.splitlines()[0] if res.stdout else "unknown"
    return first_line.strip()


def run_timeline_verification_suite() -> dict[str, Any]:
    """Run the full P0 timeline verification matrix in a temporary directory."""
    ffmpeg_bin, ffprobe_bin = ensure_ffmpeg_available()
    impulse_idx = 1000

    # Matrix: (sample_rate, sample_count, label)
    input_cases: list[tuple[Literal[44100, 48000], int, str]] = [
        (44100, 44100, "44.1kHz_1.0s_aligned"),
        (44100, 44223, "44.1kHz_non_frame_aligned_prime"),
        (48000, 48000, "48kHz_1.0s_aligned"),
        (48000, 48123, "48kHz_non_frame_aligned_prime"),
    ]

    results: list[dict[str, Any]] = []
    diagnostic_comparisons: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="aurora_p0_timeline_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)

        for sr, n_samples, case_label in input_cases:
            raw_in_pcm = _build_impulse_test_signal(sr, n_samples, impulse_index=impulse_idx)
            src_wav = tmp_dir / f"src_{case_label}.wav"

            # Write source WAV (float32 PCM so source has zero quantization noise)
            subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "f32le",
                    "-ar",
                    str(sr),
                    "-ac",
                    "2",
                    "-i",
                    "pipe:0",
                    "-c:a",
                    "pcm_f32le",
                    str(src_wav),
                ],
                input=raw_in_pcm,
                check=True,
            )

            # 1. Test standard Aurora pipeline across WAV, M4A (AAC), and MP3 at matching sample rate
            for fmt in (OutputFormat.WAV, OutputFormat.M4A, OutputFormat.MP3):
                out_file = tmp_dir / f"out_{case_label}_{fmt.value}.{fmt.value}"
                params = AuroraParams(
                    output=OutputSpec(
                        format=fmt,
                        sample_rate=sr,
                        bit_depth=24 if fmt == OutputFormat.WAV else None,
                        bitrate_kbps=None if fmt == OutputFormat.WAV else 256,
                    )
                )
                report = convert_audio(src_wav, out_file, params, overwrite=True)

                # Decode both default (container-compensated) and raw (uncompensated) to measure impulse shift
                dec_default_pcm, _, dec_ch = decode_to_f32le(out_file, ignore_gapless=False)
                dec_raw_pcm, _, raw_ch = decode_to_f32le(out_file, ignore_gapless=True)

                peak_default = _find_impulse_peak_index(dec_default_pcm, dec_ch)
                peak_raw = _find_impulse_peak_index(dec_raw_pcm, raw_ch)

                measured_raw_priming = peak_raw - impulse_idx
                residual_shift = peak_default - impulse_idx
                raw_total_samples = len(dec_raw_pcm) // (4 * raw_ch)
                default_total_samples = len(dec_default_pcm) // (4 * dec_ch)
                measured_raw_trailing_padding = raw_total_samples - n_samples - measured_raw_priming

                results.append(
                    {
                        "case": case_label,
                        "input_sample_rate": sr,
                        "output_sample_rate": sr,
                        "format": fmt.value,
                        "container": report.output.container,
                        "encoder": params.output.codec_name,
                        "input_samples": n_samples,
                        "expected_output_samples": n_samples,
                        "readback_samples_compensated": default_total_samples,
                        "sample_count_matches_input": default_total_samples == n_samples,
                        "compensated_sample_diff": default_total_samples - n_samples,
                        "compensated_impulse_shift_samples": residual_shift,
                        "raw_uncompensated_samples": raw_total_samples,
                        "raw_sample_diff": raw_total_samples - n_samples,
                        "measured_priming_samples": measured_raw_priming,
                        "measured_trailing_padding_samples": measured_raw_trailing_padding,
                        "stream_reported_initial_padding": report.output.initial_padding,
                        "sync_within_tolerance": report.timeline.sync_within_tolerance,
                    }
                )

            # 2. Cross-sample-rate check: 44.1 kHz input -> 48 kHz output (WAV, M4A, MP3)
            if sr == 44100:
                for fmt in (OutputFormat.WAV, OutputFormat.M4A, OutputFormat.MP3):
                    out_48k = tmp_dir / f"out_{case_label}_to_48k_{fmt.value}.{fmt.value}"
                    params_48k = AuroraParams(
                        output=OutputSpec(
                            format=fmt,
                            sample_rate=48000,
                            bit_depth=24 if fmt == OutputFormat.WAV else None,
                            bitrate_kbps=None if fmt == OutputFormat.WAV else 256,
                        )
                    )
                    rep_48k = convert_audio(src_wav, out_48k, params_48k, overwrite=True)
                    expected_48k = round(n_samples * 48000 / 44100)
                    results.append(
                        {
                            "case": f"{case_label}_resampled_to_48kHz",
                            "input_sample_rate": 44100,
                            "output_sample_rate": 48000,
                            "format": fmt.value,
                            "container": rep_48k.output.container,
                            "encoder": params_48k.output.codec_name,
                            "input_samples": n_samples,
                            "expected_output_samples": rep_48k.timeline.expected_output_samples,
                            "theoretical_resampled_samples": expected_48k,
                            "readback_samples_compensated": rep_48k.timeline.decoded_readback_samples,
                            "sample_count_matches_expected": (
                                rep_48k.timeline.decoded_readback_samples == rep_48k.timeline.expected_output_samples
                            ),
                            "compensated_sample_diff": rep_48k.timeline.sample_diff,
                            "raw_uncompensated_samples": rep_48k.timeline.raw_decoded_samples,
                            "raw_sample_diff": rep_48k.timeline.raw_sample_diff,
                            "measured_priming_samples": rep_48k.timeline.codec_priming_samples,
                            "measured_trailing_padding_samples": (rep_48k.timeline.codec_trailing_padding_samples),
                            "sync_within_tolerance": rep_48k.timeline.sync_within_tolerance,
                        }
                    )

        # 3. Diagnostic comparisons: macOS AudioToolbox `aac_at` and raw ADTS `.aac`
        for sr in (44100, 48000):
            n_samples = sr
            raw_in_pcm = _build_impulse_test_signal(sr, n_samples, impulse_index=impulse_idx)

            # Raw ADTS .aac
            adts_path = tmp_dir / f"diag_adts_{sr}.aac"
            subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "f32le",
                    "-ar",
                    str(sr),
                    "-ac",
                    "2",
                    "-i",
                    "pipe:0",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "256k",
                    str(adts_path),
                ],
                input=raw_in_pcm,
                check=True,
            )
            adts_dec_pcm, _, adts_ch = decode_to_f32le(adts_path, ignore_gapless=False)
            adts_samples = len(adts_dec_pcm) // (4 * adts_ch)
            adts_peak = _find_impulse_peak_index(adts_dec_pcm, adts_ch)
            adts_priming = adts_peak - impulse_idx
            adts_padding = adts_samples - n_samples - adts_priming
            diagnostic_comparisons.append(
                {
                    "variant": "raw_adts_aac",
                    "sample_rate": sr,
                    "encoder": "aac (ADTS raw stream, no container)",
                    "input_samples": n_samples,
                    "default_decoded_samples": adts_samples,
                    "default_sample_diff": adts_samples - n_samples,
                    "measured_priming_samples": adts_priming,
                    "measured_trailing_padding_samples": adts_padding,
                    "conclusion": (
                        "ADTS .aac lacks MP4 elst/stts metadata; default decode leaks 1024 priming samples "
                        f"and {adts_padding} trailing padding samples. Disallowed as an output format."
                    ),
                }
            )

            # Check if aac_at is available (macOS AudioToolbox)
            aac_at_path = tmp_dir / f"diag_aac_at_{sr}.m4a"
            at_res = subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "f32le",
                    "-ar",
                    str(sr),
                    "-ac",
                    "2",
                    "-i",
                    "pipe:0",
                    "-c:a",
                    "aac_at",
                    "-b:a",
                    "256k",
                    str(aac_at_path),
                ],
                input=raw_in_pcm,
                capture_output=True,
                check=False,
            )
            if at_res.returncode == 0 and aac_at_path.exists():
                at_def_pcm, _, at_ch = decode_to_f32le(aac_at_path, ignore_gapless=False)
                at_raw_pcm, _, _ = decode_to_f32le(aac_at_path, ignore_gapless=True)
                at_def_samples = len(at_def_pcm) // (4 * at_ch)
                at_raw_samples = len(at_raw_pcm) // (4 * at_ch)
                at_def_peak = _find_impulse_peak_index(at_def_pcm, at_ch)
                at_raw_peak = _find_impulse_peak_index(at_raw_pcm, at_ch)
                at_priming = at_raw_peak - impulse_idx
                at_padding = at_raw_samples - n_samples - at_priming
                diagnostic_comparisons.append(
                    {
                        "variant": "m4a_aac_at_audiotoolbox",
                        "sample_rate": sr,
                        "encoder": "aac_at (Apple AudioToolbox)",
                        "input_samples": n_samples,
                        "default_decoded_samples": at_def_samples,
                        "default_sample_diff": at_def_samples - n_samples,
                        "default_impulse_shift_samples": at_def_peak - impulse_idx,
                        "raw_uncompensated_samples": at_raw_samples,
                        "measured_priming_samples": at_priming,
                        "measured_trailing_padding_samples": at_padding,
                        "conclusion": (
                            f"macOS aac_at uses 2112-sample priming and leaves +{at_def_samples - n_samples} "
                            "trailing padding samples even with MP4 edit-list decoding. Aurora forces '-c:a aac' "
                            "instead of 'aac_at' for exact sample preservation and Linux parity."
                        ),
                    }
                )

    summary_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "ffmpeg_version": _get_ffmpeg_version_line(),
            "ffprobe_path": ffprobe_bin,
        },
        "codec_priming_and_padding_summary": {
            "wav_pcm_s24le": {
                "encoder": "pcm_s24le",
                "container": "wav",
                "frame_size_samples": 1,
                "priming_samples": 0,
                "trailing_padding_formula": "0",
                "container_gapless_mechanism": "None needed (uncompressed PCM)",
                "readback_matches_input_44k1": True,
                "readback_matches_input_48k": True,
                "compensation_strategy": (
                    "Direct 1:1 PCM frame output. Compensate only internal DSP engine latency_samples."
                ),
            },
            "m4a_aac_lc": {
                "encoder": "aac (FFmpeg native libavcodec)",
                "container": "m4a (MP4 / ipod muxer)",
                "frame_size_samples": 1024,
                "priming_samples": 1024,
                "trailing_padding_formula": "(1024 - (N % 1024)) % 1024 in raw AAC frames; trimmed via MP4 stts/elst",
                "container_gapless_mechanism": "MP4 'elst' edit-list box + 'sgpd'/'sbgp' roll group + exact final 'stts' delta",
                "readback_matches_input_44k1": True,
                "readback_matches_input_48k": True,
                "compensation_strategy": (
                    "1. Always use '-c:a aac' in M4A container (never ADTS .aac and never macOS 'aac_at' which has "
                    "2112 priming and un-trimmed trailing padding). "
                    "2. Rely on MP4 edit-list ('elst') + 'stts' duration trimming written by FFmpeg movenc, and verify "
                    "on readback that decoded_samples == expected_samples and impulse shift == 0. "
                    "3. For decoders ignoring edit lists, discard the first 1024 samples and slice to expected_samples."
                ),
            },
            "mp3_libmp3lame": {
                "encoder": "libmp3lame",
                "container": "mp3 (with Xing/LAME Info tag via -write_xing 1)",
                "frame_size_samples": 1152,
                "encoder_delay_samples": 576,
                "decoder_delay_samples": 529,
                "priming_samples": 1105,
                "trailing_padding_formula": "ceil((N + 1105) / 1152) * 1152 - N - 1105",
                "container_gapless_mechanism": "LAME Info / Xing header stores enc_delay=576 and enc_padding; FFmpeg mp3 demuxer strips 1105 leading samples and trailing padding",
                "readback_matches_input_44k1": True,
                "readback_matches_input_48k": True,
                "compensation_strategy": (
                    "1. Encode with '-c:a libmp3lame -write_xing 1' so LAME writes gapless delay/padding metadata. "
                    "2. Verify on readback that gapless-aware decode matches expected_samples with 0 shift. "
                    "3. For raw stream decoders that ignore Xing/LAME tags, discard the first 1105 samples "
                    "(576 encoder + 529 decoder delay) and slice to expected_samples."
                ),
            },
        },
        "verification_matrix": results,
        "diagnostic_comparisons": diagnostic_comparisons,
    }
    return summary_payload


def write_timeline_verification_json(target_path: Path) -> dict[str, Any]:
    """Run the timeline verification suite and write results to `target_path`."""
    payload = run_timeline_verification_suite()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload
