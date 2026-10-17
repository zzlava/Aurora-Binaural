"""F-P3-1: reclaim stale attempts without touching an active retry or other files."""

from pathlib import Path

import pytest

from aurora_binaural.media import probe_audio
from aurora_binaural.params import AuroraParams, InputSpec
from aurora_binaural.storage import TaskStore
from aurora_binaural.worker import AuroraWorker
from test_aurora_p3 import _make_test_config, _write_test_wav


@pytest.mark.parametrize("with_file", [False, True])
def test_cleanup_expired_attempt_protects_active_retry(tmp_path: Path, with_file: bool) -> None:
    cfg = _make_test_config(tmp_path)
    cfg.ensure_directories()
    store = TaskStore(cfg.db_path)
    audio = cfg.assets_dir / "input.wav"
    _write_test_wav(audio, samples=4800)
    store.create_asset(
        asset_id="asset_test",
        filename="input.wav",
        file_path=audio,
        probe=probe_audio(audio),
        retention_sec=3600,
        now=100,
    )
    job, _ = store.create_or_get_job(AuroraParams(input=InputSpec(asset_id="asset_test")), retention_sec=3600, now=100)
    first = store.claim_next_job("killed", lease_duration_sec=1, now=100)
    assert first is not None
    old_dir = cfg.tmp_dir / f"attempt_{job.job_id}_1_{first.lease_id}"
    old_dir.mkdir()
    if with_file:
        (old_dir / "partial.wav").write_bytes(b"partial")
    worker = AuroraWorker(cfg, store=store)
    worker.recover_and_cleanup(now=100.5)
    assert old_dir.exists()
    store.recover_expired_leases(now=102)
    retry = store.claim_next_job("alive", lease_duration_sec=30, now=102)
    assert retry is not None
    active_dir = cfg.tmp_dir / f"attempt_{job.job_id}_2_{retry.lease_id}"
    active_dir.mkdir()
    (active_dir / "working").write_bytes(b"active")
    unrelated = cfg.tmp_dir / "attempt_unrelated"
    unrelated.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    link = cfg.tmp_dir / f"attempt_{job.job_id}_3_lease_{'f' * 32}"
    link.symlink_to(external, target_is_directory=True)
    worker.recover_and_cleanup(now=103)
    assert not old_dir.exists()
    assert (active_dir / "working").read_bytes() == b"active"
    assert unrelated.exists() and link.is_symlink() and external.exists()
    worker.recover_and_cleanup(now=103)
    assert active_dir.exists()
