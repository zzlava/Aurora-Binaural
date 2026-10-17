"""Equal sample counts, bit-exact native bypass, exact bypass origin and <=1 ms spatial peaks."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aurora_binaural.catalog import DEFAULT_PRESET_ID
from aurora_binaural.engine import RustRenderer, run_offline_block_pipeline
from aurora_binaural.exit_codes import ProcessingError
from aurora_binaural.media import decode_to_f32le, encode_f32le_to_path, require_soxr
from aurora_binaural.params import AuroraParams, OutputSpec
from aurora_binaural.pipeline import convert_audio


@pytest.mark.parametrize('rate', [44100, 48000, 88200, 96000, 176400, 192000])
@pytest.mark.parametrize(('fmt', 'depth'), [('flac', 24), ('wav', '32f')])
@pytest.mark.parametrize('strength', [0.0, 0.5])
def test_real_route_v_source_rate_counts_and_resampling_origin(
    tmp_path: Path, rate: int, fmt: str, depth: Any, strength: float,
    record_property: Callable[[str, object], None],
) -> None:
    pytest.importorskip('aurora_core')
    n, position = rate + 17, rate // 2 + 17
    signal = np.zeros((n, 2), dtype='<f4')
    signal[position] = 0.25
    spec = OutputSpec.model_validate({'format': fmt, 'bit_depth': depth})
    source, output = tmp_path / f'source.{fmt}', tmp_path / f'result.{fmt}'
    encode_f32le_to_path(signal.tobytes(), sample_rate=rate, channels=2, output_spec=spec, target_path=source)
    params = AuroraParams(preset=DEFAULT_PRESET_ID, strength=strength, output=spec)
    report = convert_audio(source, output, params, renderer=RustRenderer())
    raw, sr, channels = decode_to_f32le(output)
    readback = np.frombuffer(raw, dtype='<f4').reshape(-1, channels)
    assert report.input.sample_rate == sr == rate
    assert report.engine.internal_sample_rate == 48000
    assert report.input.samples == report.output.samples == n
    assert report.timeline.expected_output_samples == n
    assert report.timeline.sample_diff == 0
    peak_offset = int(np.argmax(np.max(np.abs(readback), axis=1))) - position
    record_property('peak_offset_source_samples', peak_offset)
    record_property('peak_offset_ms', peak_offset * 1000 / rate)
    if strength == 0.0:
        assert peak_offset == 0
        # Soxr boundaries alter amplitudes at non-48k source rates. Verify native
        # bypass bytes against the actual 48k input independently of resampling.
        processing_pcm, processing_sr, processing_ch = decode_to_f32le(source, target_sample_rate=48000)
        bypass, _, _, _, _ = run_offline_block_pipeline(
            processing_pcm, sample_rate=processing_sr, input_channels=processing_ch,
            params=params, renderer=RustRenderer(),
        )
        assert bypass == processing_pcm
        if rate == 48000:
            assert raw == decode_to_f32le(source)[0]
    else:
        assert abs(peak_offset) * 1000 <= rate  # <=1 ms in source-rate units


def test_missing_soxr_has_actionable_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A shell fixture models FFmpeg without soxr; fail closed before using a lower-quality engine.
    binary = tmp_path / 'ffmpeg-without-soxr'
    binary.write_text('#!/bin/sh\nexit 1\n')
    binary.chmod(0o755)
    with pytest.raises(ProcessingError) as error:
        require_soxr(str(binary))
    assert error.value.error_code == 'SOXR_UNAVAILABLE'
    assert 'AURORA_FFMPEG' in str(error.value)
