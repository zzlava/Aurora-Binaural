"""Reproduce P6-02 timeline acceptance and isolate native-engine loudness.

AURORA_FFMPEG=/path/to/soxr/ffmpeg .venv/bin/python qa/p6/codex/verify_02.py
Exit 1 means the strict spatial impulse acceptance failed; evidence is still saved.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from aurora_binaural.catalog import DEFAULT_PRESET_ID
from aurora_binaural.engine import RenderConfig, RustRenderer, StubRenderer
from aurora_binaural.media import (
    decode_to_f32le,
    encode_f32le_to_path,
    ensure_ffmpeg_available,
    measure_loudness_file,
    require_soxr,
)
from aurora_binaural.params import AuroraParams, OutputSpec
from aurora_binaural.pipeline import convert_audio

ROOT = Path(__file__).resolve().parents[3]
QA = Path(__file__).resolve().parent
RUNTIME = QA / '.runtime' / 'p6-02'


def pcm(path: Path) -> np.ndarray:
    raw, _, channels = decode_to_f32le(path)
    return np.frombuffer(raw, dtype='<f4').reshape(-1, channels)


def timeline() -> list[dict]:
    rows = []
    for rate in (44100, 88200, 96000, 192000):
        n = rate * 2 + 17  # Deliberately not an integer 48 kHz frame count.
        position = rate // 2 + 17
        signal = np.zeros((n, 2), dtype='<f4')
        signal[position] = 0.25
        for fmt, depth in (('wav', '32f'), ('flac', 24)):
            spec = OutputSpec.model_validate({'format': fmt, 'bit_depth': depth})
            source = RUNTIME / f'impulse-{rate}.{fmt}'
            encode_f32le_to_path(signal.tobytes(), sample_rate=rate, channels=2,
                                 output_spec=spec, target_path=source)
            for label, strength in (('spatial', 0.5), ('native-bypass', 0.0)):
                output = RUNTIME / f'{label}-{rate}.{fmt}'
                report = convert_audio(
                    source, output, AuroraParams(preset='V2', strength=strength, output=spec),
                    renderer=RustRenderer(), overwrite=True,
                )
                samples = pcm(output)
                peak = int(np.argmax(np.max(np.abs(samples), axis=1)))
                rows.append({
                    'input_format': fmt, 'output_format': fmt, 'preset': 'V2', 'strength': strength,
                    'mode': label, 'rate_chain': [report.input.sample_rate, report.engine.internal_sample_rate,
                                                  report.output.sample_rate],
                    'source_samples': n, 'processing_samples': report.timeline.processing_input_samples,
                    'output_samples': len(samples), 'sample_diff': len(samples) - n,
                    'duration_rounding_adjustment_samples': report.timeline.duration_rounding_adjustment_samples,
                    'source_peak': position, 'output_peak': peak, 'peak_error_source_samples': peak - position,
                    'sample_count_pass': abs(len(samples) - n) <= 1,
                    'impulse_pass': abs(peak - position) <= 1,
                    'engine_latency_48k': report.timeline.engine_latency_samples,
                })
    return rows


def loudness() -> dict:
    source = ROOT / 'data/assets/real_music.wav'
    signal = pcm(source)
    # Match the previous accepted P6 report explicitly, independent of TOML default changes.
    params = AuroraParams(preset='V2', strength=0.5, output=OutputSpec(bit_depth='32f'))
    paths = {label: RUNTIME / f'real_music-{label}.wav' for label in ('stub', 'spatial', 'direct-core')}
    stub = convert_audio(source, paths['stub'], params, renderer=StubRenderer(), overwrite=True)
    spatial = convert_audio(source, paths['spatial'], params, renderer=RustRenderer(), overwrite=True)
    # Independent direct core: no RendererStreamProcessor, no application fade or encode gain.
    direct = RustRenderer()
    direct.prepare(48000, 1024, RenderConfig())
    direct.set_params(params)
    latency = direct.latency_samples()
    blocks = [direct.process(signal[start:start + 1024]) for start in range(0, len(signal), 1024)]
    blocks.append(direct.flush())
    raw = np.concatenate(blocks)[latency:latency + len(signal)]
    encode_f32le_to_path(raw.tobytes(), sample_rate=48000, channels=2,
                         output_spec=OutputSpec(bit_depth='32f'), target_path=paths['direct-core'])
    direct_metrics = measure_loudness_file(paths['direct-core'])
    app_pcm = pcm(paths['spatial'])
    stub_pcm = pcm(paths['stub'])
    # Spatial trim mode deliberately fades the final 10 ms; compare every preceding bit.
    body = max(0, len(raw) - 480)
    return {
        'source': str(source.relative_to(ROOT)), 'preset': 'V2', 'strength': 0.5,
        'source_decoded_sha256': hashlib.sha256(signal.tobytes()).hexdigest(),
        'stub_decoded_sha256': hashlib.sha256(stub_pcm.tobytes()).hexdigest(),
        'stub_bit_exact': signal.tobytes() == stub_pcm.tobytes(),
        'direct_core_vs_app_body_bit_exact': raw[:body].tobytes() == app_pcm[:body].tobytes(),
        'compared_body_samples': body, 'spatial_fade_samples_excluded': len(raw) - body,
        'input': stub.loudness_before.model_dump(), 'stub': stub.loudness_after.model_dump(),
        'spatial': spatial.loudness_after.model_dump(), 'direct_core': direct_metrics.model_dump(),
        'spatial_minus_input_lu': round(spatial.loudness_after.integrated_lufs - stub.loudness_before.integrated_lufs, 2),
        'application_export_gain_db': 0.0,
    }


def native_impulse_reference() -> dict:
    position = 24017
    signal = np.zeros((96017, 2), dtype=np.float32)
    signal[position] = 0.25
    renderer = RustRenderer()
    renderer.prepare(48000, 1024, RenderConfig())
    renderer.set_params(AuroraParams(preset='V2', strength=0.5))
    latency = renderer.latency_samples()
    blocks = [renderer.process(signal[start:start + 1024]) for start in range(0, len(signal), 1024)]
    blocks.append(renderer.flush())
    direct = np.concatenate(blocks)[latency:latency + len(signal)]
    peak = int(np.argmax(np.max(np.abs(direct), axis=1)))
    return {'rate': 48000, 'reported_latency': latency, 'source_peak': position, 'output_peak': peak,
            'peak_error_samples': peak - position, 'application_stream_processor_used': False}


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = ensure_ffmpeg_available()
    require_soxr(ffmpeg)
    rows = timeline()
    result = {
        'generated_at_utc': datetime.now(UTC).isoformat(),
        'application_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'ffmpeg': ffmpeg, 'ffprobe': ffprobe,
        'ffmpeg_version': subprocess.check_output([ffmpeg, '-version'], text=True).splitlines()[0],
        'resampler': 'aresample=resampler=soxr:precision=28', 'default_preset_in_this_checkout': DEFAULT_PRESET_ID,
        'timeline': rows, 'native_impulse_reference': native_impulse_reference(), 'loudness': loudness(),
        'strict_spatial_timeline_pass': all(row['sample_count_pass'] and row['impulse_pass']
                                            for row in rows if row['mode'] == 'spatial'),
    }
    (QA / 'evidence-02.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['strict_spatial_timeline_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
