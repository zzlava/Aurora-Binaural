"""Preview API/worker contract and source-time regression tests."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aurora_binaural.api import create_app
from aurora_binaural.engine import FloatBlock, StubRenderer, run_offline_block_pipeline
from aurora_binaural.media import decode_to_f32le
from aurora_binaural.storage import TaskStore
from aurora_binaural.worker import AuroraWorker
from test_aurora_p3 import _make_test_config, _write_test_wav

AUTH = {'Authorization': 'Bearer test_secret_api_key_p3'}


def upload(client: TestClient, tmp_path: Path, *, channels: int = 2, amplitude: float = 0.25) -> str:
    content = _write_test_wav(tmp_path / 'source.wav', samples=48000 * 3, channels=channels, amplitude=amplitude)
    result = client.post('/v1/assets', content=content, headers=AUTH)
    assert result.status_code == 201
    return str(result.json()['asset_id'])


@pytest.mark.parametrize('channels,start,duration,expected', [(2, 1.0, 1.0, 48000), (1, 2.5, 1.0, 24000)])
def test_preview_pair_loudness_timeline_and_expiry(tmp_path: Path, channels: int, start: float,
                                                  duration: float, expected: int) -> None:
    cfg = _make_test_config(tmp_path)
    store = TaskStore(cfg.db_path)
    client = TestClient(create_app(cfg, store=store))
    asset_id = upload(client, tmp_path, channels=channels)
    body = {'asset_id': asset_id, 'start_seconds': start, 'duration_seconds': duration}
    created = client.post('/v1/previews', json=body, headers=AUTH)
    assert created.status_code == 202
    job_id = created.json()['job_id']
    assert client.get(f'/v1/jobs/{job_id}/original', headers=AUTH).status_code == 409
    result = AuroraWorker(cfg, store=store).run_once()
    assert result is not None and result.status == 'succeeded'
    report = client.get(f'/v1/jobs/{job_id}/report', headers=AUTH).json()
    assert report['preview']['samples'] == expected
    assert report['preview']['start_sample'] == round(start * 48000)
    assert report['preview']['loudness_diff_lu'] <= 0.2
    assert report['preview']['match_status'] == 'matched'
    assert report['preview']['timeline_strategy'] == 'render-from-source-origin'
    assert report['engine']['is_stub'] is True
    assert 'SADIE II' in report['engine']['hrtf']['name']
    assert str(cfg.data_dir) not in str(report)
    assert client.get(f'/v1/jobs/{job_id}/original', headers=AUTH).status_code == 200
    assert client.get(f'/v1/jobs/{job_id}/result', headers=AUTH).status_code == 200
    assert client.get(f'/v1/jobs/{job_id}/original').status_code == 401
    original = cfg.results_dir / f'{job_id}.original.wav'
    assert original.exists()
    store.cleanup_expired_resources(now=time.time() + 7200)
    assert not original.exists()
    assert client.get(f'/v1/jobs/{job_id}/original', headers=AUTH).status_code == 410


def test_preview_validation_auth_and_idempotency(tmp_path: Path) -> None:
    cfg = _make_test_config(tmp_path)
    client = TestClient(create_app(cfg))
    asset = upload(client, tmp_path)
    body = {'asset_id': asset, 'start_seconds': 0, 'duration_seconds': 1}
    assert client.post('/v1/previews', json=body).status_code == 401
    for extra in ({'duration_seconds': 21}, {'duration_seconds': 0}, {'start_seconds': -1},
                  {'start_seconds': 3}, {'start_seconds': 'NaN'}, {'preset': {'id': 'orbit', 'version': '999'}}):
        assert client.post('/v1/previews', json=body | extra, headers=AUTH).status_code == 422
    assert client.post('/v1/previews', json=body | {'asset_id': 'missing'}, headers=AUTH).status_code == 404
    headers = AUTH | {'Idempotency-Key': 'preview-example'}
    first = client.post('/v1/previews', json=body, headers=headers)
    assert first.status_code == 202
    assert client.post('/v1/previews', json=body, headers=headers).json()['job_id'] == first.json()['job_id']
    assert client.post('/v1/previews', json=body | {'start_seconds': 1}, headers=headers).status_code == 409
    export = client.post('/v1/jobs', json={'input': {'asset_id': asset}}, headers=headers)
    assert export.status_code == 409
    canceled = client.post(f"/v1/jobs/{first.json()['job_id']}/cancel", headers=AUTH)
    assert canceled.json()['status'] == 'canceled'
    assert AuroraWorker(cfg).run_once() is None


class MovingTestRenderer(StubRenderer):
    """Stateful time-varying core: restarting at the excerpt produces a different signal."""
    def reset(self) -> None:
        super().reset()
        self.position = 0

    def process(self, block: FloatBlock) -> FloatBlock:
        result = super().process(block)
        phase = (np.arange(len(block)) + self.position) / 48000
        gains = (0.5 + 0.4 * np.sin(phase * 2.0)).astype(np.float32)
        self.position += len(block)
        return result * gains[:, None]


def test_motion_preview_uses_source_origin_and_same_seed(tmp_path: Path) -> None:
    cfg = _make_test_config(tmp_path)
    store = TaskStore(cfg.db_path)
    client = TestClient(create_app(cfg, store=store))
    asset = upload(client, tmp_path)
    created = client.post('/v1/previews', json={'asset_id': asset, 'start_seconds': 1,
                          'duration_seconds': 1, 'preset': {'id': 'orbit'}, 'seed': 1234}, headers=AUTH)
    job_id = created.json()['job_id']
    finished = AuroraWorker(cfg, store=store, renderer_factory=MovingTestRenderer).run_once()
    assert finished is not None and finished.status == 'succeeded'
    pcm, sr, ch = decode_to_f32le(tmp_path / 'source.wav')
    full, _, _, _, _ = run_offline_block_pipeline(pcm, sample_rate=sr, input_channels=ch,
                                                  params=finished.params, renderer=MovingTestRenderer())
    expected = np.frombuffer(full, dtype=np.float32).reshape(-1, 2)[48000:96000]
    actual_bytes, _, _ = decode_to_f32le(cfg.results_dir / f'{job_id}.wav')
    actual = np.frombuffer(actual_bytes, dtype=np.float32).reshape(-1, 2)
    # Loudness matching applies a constant gain; the source-time waveform must agree.
    ratio = float(np.sum(actual * expected) / np.sum(expected * expected))
    np.testing.assert_allclose(actual, expected * ratio, atol=2e-6)
    assert finished.params.seed == 1234


def test_preview_digital_silence_is_explicit(tmp_path: Path) -> None:
    cfg = _make_test_config(tmp_path)
    client = TestClient(create_app(cfg))
    asset = upload(client, tmp_path, amplitude=0)
    client.post('/v1/previews', json={'asset_id': asset, 'start_seconds': 1, 'duration_seconds': 1}, headers=AUTH)
    finished = AuroraWorker(cfg).run_once()
    assert finished is not None and finished.status == 'succeeded'
    report = client.get(f'/v1/jobs/{finished.job_id}/report', headers=AUTH).json()
    assert report['preview']['match_status'] == 'both_silent'
    assert report['preview']['target_lufs'] is None
