"""Source-rate sample counts and resampler alignment through the real route V adapter.

Full spatial peak acceptance is separately enforced by qa/p6/codex/verify_02.py;
the upstream route V FIR's known peak advance is not hidden in these checks.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from aurora_binaural.engine import RustRenderer
from aurora_binaural.exit_codes import ProcessingError
from aurora_binaural.media import decode_to_f32le, encode_f32le_to_path, require_soxr
from aurora_binaural.params import AuroraParams, OutputSpec
from aurora_binaural.pipeline import convert_audio


@pytest.mark.parametrize('rate', [44100, 88200, 96000, 176400, 192000])
@pytest.mark.parametrize(('fmt', 'depth'), [('flac', 24), ('wav', '32f')])
@pytest.mark.parametrize('strength', [0.0, 0.5])
def test_real_route_v_source_rate_counts_and_resampling_origin(
    tmp_path: Path, rate: int, fmt: str, depth: Any, strength: float,
) -> None:
    pytest.importorskip('aurora_core')
    n, position = rate + 17, rate // 2 + 17
    signal = np.zeros((n, 2), dtype='<f4')
    signal[position] = 0.25
    spec = OutputSpec.model_validate({'format': fmt, 'bit_depth': depth})
    source, output = tmp_path / f'source.{fmt}', tmp_path / f'result.{fmt}'
    encode_f32le_to_path(signal.tobytes(), sample_rate=rate, channels=2, output_spec=spec, target_path=source)
    report = convert_audio(source, output, AuroraParams(preset='V2', strength=strength, output=spec), renderer=RustRenderer())
    raw, sr, channels = decode_to_f32le(output)
    readback = np.frombuffer(raw, dtype='<f4').reshape(-1, channels)
    assert report.input.sample_rate == sr == rate
    assert report.engine.internal_sample_rate == 48000
    assert report.input.samples == report.output.samples == n
    assert report.timeline.expected_output_samples == n
    assert report.timeline.sample_diff == 0
    if strength == 0.0:
        assert abs(int(np.argmax(np.max(np.abs(readback), axis=1))) - position) <= 1


def test_missing_soxr_has_actionable_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A shell fixture models FFmpeg without soxr; fail closed before using a lower-quality engine.
    binary = tmp_path / 'ffmpeg-without-soxr'
    binary.write_text('#!/bin/sh\nexit 1\n')
    binary.chmod(0o755)
    with pytest.raises(ProcessingError) as error:
        require_soxr(str(binary))
    assert error.value.error_code == 'SOXR_UNAVAILABLE'
    assert 'AURORA_FFMPEG' in str(error.value)
