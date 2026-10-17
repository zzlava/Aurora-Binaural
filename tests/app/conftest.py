"""Legacy application regression suite runs the explicitly selected stub engine."""
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def explicit_stub_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AURORA_ENGINE", "stub")
    yield
