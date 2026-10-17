"""Round-trip codec, metadata/artwork and public surface checks for ADR 0006."""

import json
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aurora_binaural.api import create_app
from aurora_binaural.cli import run_cli
from aurora_binaural.config import ServiceConfig
from aurora_binaural.engine import StubRenderer
from aurora_binaural.media import decode_to_f32le, ensure_ffmpeg_available, probe_audio
from aurora_binaural.params import AuroraParams, OutputSpec
from aurora_binaural.pipeline import convert_audio
from aurora_binaural.worker import AuroraWorker


def ffmpeg(*args: str, pcm: bytes | None = None) -> None:
    binary, _ = ensure_ffmpeg_available()
    subprocess.run([binary, '-v', 'error', '-y', *args], input=pcm, capture_output=True, check=True)


def make_source(path: Path, *, rate: int = 48000, frames: int = 48017, codec: str = 'pcm_f32le') -> None:
    t = np.arange(frames) / rate
    pcm = np.column_stack((0.08 * np.sin(2 * np.pi * 440 * t), 0.06 * np.sin(2 * np.pi * 660 * t)))
    ffmpeg('-f', 'f32le', '-ar', str(rate), '-ac', '2', '-i', 'pipe:0', '-c:a', codec, str(path),
           pcm=pcm.astype('<f4').tobytes())


def probe_all(path: Path) -> dict[str, Any]:
    _, binary = ensure_ffmpeg_available()
    return json.loads(subprocess.check_output([binary, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))  # type: ignore[no-any-return]


@pytest.fixture
def tagged_source(tmp_path: Path) -> Path:
    source = tmp_path / 'source.wav'
    make_source(source)
    # A deterministic, valid PNG built without an imaging dependency.
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    cover = tmp_path / 'cover.png'
    cover.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 8, 8, 2, 0, 0, 0))
                      + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x14\x68\x89' * 8) * 8)) + chunk(b'IEND', b''))
    tagged = tmp_path / 'tagged.flac'
    ffmpeg('-i', str(source), '-i', str(cover), '-map', '0:a', '-map', '1:v', '-c:a', 'flac', '-c:v', 'copy',
           '-disposition:v', 'attached_pic', '-metadata', 'title=Aurora title', '-metadata', 'artist=Aurora artist',
           '-metadata', 'album=Aurora album', str(tagged))
    return tagged


@pytest.mark.parametrize(('fmt', 'depth', 'codec'), [
    ('flac', 16, 'flac'), ('flac', 24, 'flac'), ('wav', 16, 'pcm_s16le'), ('wav', 24, 'pcm_s24le'),
    ('wav', '32f', 'pcm_f32le'), ('alac', 16, 'alac'), ('alac', 24, 'alac'),
    ('aiff', 16, 'pcm_s16be'), ('aiff', 24, 'pcm_s24be'), ('m4a', None, 'aac'), ('mp3', None, 'mp3'),
])
def test_formats_preserve_tags_and_artwork(tagged_source: Path, tmp_path: Path, fmt: str, depth: Any, codec: str) -> None:
    spec = OutputSpec.model_validate({'format': fmt, 'bit_depth': depth})
    output = tmp_path / f'result.{spec.extension}'
    report = convert_audio(tagged_source, output, AuroraParams(output=spec), renderer=StubRenderer())
    info = probe_all(output)
    tags = {k.lower(): v for k, v in info['format'].get('tags', {}).items()}
    assert {k: tags[k] for k in ('title', 'artist', 'album')} == {
        'title': 'Aurora title', 'artist': 'Aurora artist', 'album': 'Aurora album',
    }
    assert any(s.get('disposition', {}).get('attached_pic') for s in info['streams'])
    assert report.output.codec == codec
    assert report.output.sample_rate == 48000
    assert report.timeline.sync_within_tolerance
    if spec.is_lossless:
        assert report.output.samples == report.input.samples
        assert report.output.bit_depth == (32 if depth == '32f' else depth)
    # The produced format is also a usable input, including artwork-bearing WAV/AIFF/ALAC.
    assert probe_audio(output).samples == report.output.samples


@pytest.mark.parametrize('fmt', ['flac', 'alac', 'aiff'])
def test_float_rejected_for_integer_lossless_formats(fmt: str) -> None:
    with pytest.raises(ValidationError, match='32f'):
        OutputSpec.model_validate({'format': fmt, 'bit_depth': '32f'})


def test_cli_and_api_flac_source_export(tagged_source: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / 'cli.flac'
    assert run_cli(['convert', str(tagged_source), '--output', str(output), '--format', 'flac',
                    '--sample-rate', 'source', '--bit-depth', '16']) == 0
    cli = json.loads(capsys.readouterr().out)
    assert cli['params']['output']['sample_rate'] == 'source'
    assert cli['output']['codec'] == 'flac' and cli['output']['bit_depth'] == 16
    cfg = ServiceConfig(api_key='fixture', data_dir=tmp_path / 'runtime', db_path=tmp_path / 'db')
    app = create_app(cfg)
    headers = {'Authorization': 'Bearer fixture'}
    with TestClient(app) as client:
        asset = client.post('/v1/assets', headers=headers,
                            files={'file': (tagged_source.name, tagged_source.read_bytes())})
        assert asset.status_code == 201
        for fmt, extension, mime in [('flac', 'flac', 'audio/flac'), ('alac', 'm4a', 'audio/mp4'), ('aiff', 'aiff', 'audio/aiff')]:
            job = client.post('/v1/jobs', headers=headers, json={
                'input': {'asset_id': asset.json()['asset_id']},
                'output': {'format': fmt, 'sample_rate': 'source', 'bit_depth': 24},
            })
            assert job.status_code == 202
            completed = AuroraWorker(cfg, store=app.state.store).run_once()
            assert completed is not None and completed.status == 'succeeded', completed
            result = client.get(f'/v1/jobs/{completed.job_id}/result', headers=headers)
            assert result.status_code == 200 and result.headers['content-type'] == mime
            assert f'.{extension}' in result.headers['content-disposition']


def test_16bit_tpdf_and_24bit_no_dither(tmp_path: Path) -> None:
    source = tmp_path / 'silence.wav'
    ffmpeg('-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0', '-c:a', 'pcm_f32le', str(source),
           pcm=np.zeros((48000, 2), dtype='<f4').tobytes())
    for depth in (16, 24):
        output = tmp_path / f'dither-{depth}.flac'
        convert_audio(source, output, AuroraParams(output=OutputSpec(format='flac', bit_depth=depth)), renderer=StubRenderer())
        pcm, _, _ = decode_to_f32le(output)
        samples = np.frombuffer(pcm, dtype='<f4')
        if depth == 16:
            values, counts = np.unique(samples * 32768, return_counts=True)
            assert list(values) == [-1, 0, 1]
            assert counts[0] / len(samples) == pytest.approx(0.125, abs=0.015)
            assert counts[-1] / len(samples) == pytest.approx(0.125, abs=0.015)
        else:
            assert np.count_nonzero(samples) == 0
