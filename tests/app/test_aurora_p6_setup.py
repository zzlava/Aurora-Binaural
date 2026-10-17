"""Capability discovery must work without shell configuration in GUI processes."""

from pathlib import Path

import pytest

from aurora_binaural import ffmpeg_discovery as discovery
from aurora_binaural.exit_codes import ProcessingError
from aurora_binaural.media import ensure_ffmpeg_available, require_soxr


def binary(directory: Path, name: str, *, soxr: bool = True) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f'#!/bin/sh\nif [ "$1" = "-version" ]; then exit 0; fi\nexit {0 if soxr else 1}\n')
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def discovery_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("AURORA_FFMPEG", raising=False)
    monkeypatch.delenv("AURORA_FFPROBE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "path"))
    monkeypatch.setattr(discovery, "LOCAL_ENV_PATH", tmp_path / ".aurora-local.env")
    monkeypatch.setattr(discovery, "HOMEBREW_FFMPEG_DIR", tmp_path / "full")
    discovery._works.cache_clear()
    return tmp_path


def test_path_then_homebrew_soxr_and_probe_fallback(discovery_env: Path) -> None:
    binary(discovery_env / "path", "ffmpeg", soxr=False)
    full = binary(discovery_env / "full", "ffmpeg")
    probe = binary(discovery_env / "full", "ffprobe")
    assert ensure_ffmpeg_available() == (full, probe)
    path = binary(discovery_env / "path", "ffmpeg")
    path_probe = binary(discovery_env / "path", "ffprobe")
    discovery._works.cache_clear()
    assert ensure_ffmpeg_available() == (path, path_probe)


def test_local_config_roundtrip_from_other_cwd_and_stale_fallback(
    discovery_env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = binary(discovery_env / "full with spaces", "ffmpeg")
    probe = binary(discovery_env / "full with spaces", "ffprobe")
    monkeypatch.setattr(discovery, "HOMEBREW_FFMPEG_DIR", Path(full).parent)
    discovery.configure_local_ffmpeg()
    monkeypatch.chdir(discovery_env.parent)
    monkeypatch.setattr(discovery, "HOMEBREW_FFMPEG_DIR", discovery_env / "missing")
    assert ensure_ffmpeg_available() == (full, probe)
    Path(full).unlink()
    Path(probe).unlink()
    fallback = binary(discovery_env / "path", "ffmpeg")
    fallback_probe = binary(discovery_env / "path", "ffprobe")
    assert ensure_ffmpeg_available() == (fallback, fallback_probe)


def test_explicit_override_is_not_silently_replaced(discovery_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = binary(discovery_env / "path", "ffmpeg", soxr=False)
    binary(discovery_env / "full", "ffmpeg")
    binary(discovery_env / "path", "ffprobe")
    monkeypatch.setenv("AURORA_FFMPEG", path)
    assert ensure_ffmpeg_available()[0] == path
    with pytest.raises(ProcessingError, match="brew install ffmpeg-full") as failure:
        require_soxr(path)
    assert failure.value.error_code == "SOXR_UNAVAILABLE"
    monkeypatch.setenv("AURORA_FFPROBE", str(discovery_env / "missing-probe"))
    with pytest.raises(ProcessingError) as failure:
        ensure_ffmpeg_available()
    assert failure.value.error_code == "FFMPEG_NOT_FOUND"


def test_no_soxr_keeps_48k_usable_but_setup_fails(discovery_env: Path) -> None:
    path = binary(discovery_env / "path", "ffmpeg", soxr=False)
    binary(discovery_env / "path", "ffprobe")
    assert ensure_ffmpeg_available()[0] == path
    with pytest.raises(SystemExit, match="soxr"):
        discovery.configure_local_ffmpeg()
    assert not discovery.LOCAL_ENV_PATH.exists()


def test_config_is_never_executed(discovery_env: Path) -> None:
    sentinel = discovery_env / "executed"
    discovery.LOCAL_ENV_PATH.write_text(f'AURORA_FFMPEG=$(touch "{sentinel}")\nAURORA_FFPROBE="unterminated\n')
    binary(discovery_env / "path", "ffmpeg")
    binary(discovery_env / "path", "ffprobe")
    ensure_ffmpeg_available()
    assert not sentinel.exists()
